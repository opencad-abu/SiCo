"""Migration admission must preserve legacy bytes and reject uncertain ownership."""

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sico.core.contracts import BoundContext, RunState
from sico.migration.audit import audit
from legacy_state_fixtures import HistoricalJournal as SessionJournal


def codes(report):
    return {row["code"] for row in report["blockers"]}


def write(root, relative, value):
    path = root / ".cad" / relative
    directory = root
    for part in path.parent.relative_to(root).parts:
        directory = directory / part
        directory.mkdir(mode=0o700, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")
    path.chmod(0o600)
    return path


def session(root):
    (root / ".cad").mkdir(mode=0o700, exist_ok=True)
    with SessionJournal(root, "session") as journal:
        artifact = journal.text_attachment("immutable evidence")
        journal.append("test", {"artifact": artifact},
                       RunState(BoundContext("instance", "generation", "target", {})))
        return journal.directory / "events.jsonl", journal.root / artifact["path"]


@pytest.fixture(autouse=True)
def isolated_process_observation(monkeypatch):
    # These tests control locks and records; process discovery has separate real tests.
    monkeypatch.setattr("sico.migration.audit.scan_local", lambda source: {
        "scope": "local", "referencing_pids": [], "unreadable_processes": 0})


def test_audit_is_repeatable_read_only_and_preserves_nas_spelling(tmp_path):
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "NAS alias"
    alias.symlink_to(physical, target_is_directory=True)
    journal, artifact = session(alias)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (journal, artifact)}
    report = audit(alias)
    assert not report["blockers"]
    assert report["eligible_for_stopped_migration"]
    assert report["cutover_authorized"] is False
    assert report["source"] == str(alias / ".cad")
    assert report["inventory_sha256"] == audit(alias)["inventory_sha256"]
    assert not (alias / ".sico").exists()
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before} == before


def test_active_journal_and_extensionless_background_lease(tmp_path):
    (tmp_path / ".cad").mkdir(mode=0o700)
    with SessionJournal(tmp_path, "session"):
        assert "active_writer" in codes(audit(tmp_path))
    path = write(tmp_path, "ai/agent/background/services/" + "a" * 32, {})
    with path.open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert "active_writer" in codes(audit(tmp_path))
    assert "active_writer" not in codes(audit(tmp_path))


@pytest.mark.parametrize("damage", ["partial", "sequence", "attachment"])
def test_damaged_history_is_never_repaired_by_audit(tmp_path, damage):
    journal, artifact = session(tmp_path)
    if damage == "partial":
        with journal.open("ab") as stream:
            stream.write(b'{"unfinished":')
    elif damage == "sequence":
        row = json.loads(journal.read_bytes())
        row["sequence"] = 2
        journal.write_text(json.dumps(row) + "\n")
    else:
        artifact.write_text("changed")
    before = journal.read_bytes(), artifact.read_bytes()
    assert "invalid_or_changed_record" in codes(audit(tmp_path))
    assert (journal.read_bytes(), artifact.read_bytes()) == before


def test_unknown_and_runtime_trees_are_not_traversed_or_copied(tmp_path):
    session(tmp_path)
    root = tmp_path / ".cad"
    for name in ("worktrees", "cache", "runtime", "unknown", "ai/terminal-logs"):
        directory = root / name
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        (directory / "unreadable").mkdir(mode=0)
    try:
        report = audit(tmp_path)
    finally:
        for name in ("worktrees", "cache", "runtime", "unknown", "ai/terminal-logs"):
            (root / name / "unreadable").chmod(0o700)
    paths = {row["path"] for row in report["entries"]}
    assert not any("unreadable" in path for path in paths)
    assert codes(report) >= {"unverified_runtime", "unregistered_directory",
                             "terminal_history_adapter_required"}
    actions = {row["path"]: row["action"] for row in report["entries"]}
    assert actions["worktrees"] == "preserve"
    assert actions["cache"] == "rebuild"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_unsafe_file_never_becomes_a_retained_input(tmp_path, kind):
    journal, artifact = session(tmp_path)
    artifact.unlink()
    if kind == "symlink":
        artifact.symlink_to(journal)
    elif kind == "hardlink":
        os.link(journal, artifact)
    else:
        os.mkfifo(artifact, 0o600)
    report = audit(tmp_path)
    assert report["blockers"]
    entry = next(row for row in report["entries"] if row["path"].endswith(artifact.name))
    assert "sha256" not in entry


def test_existing_target_is_never_merged_even_when_empty(tmp_path):
    session(tmp_path)
    (tmp_path / ".sico").mkdir()
    assert "target_exists" in codes(audit(tmp_path))
    assert list((tmp_path / ".sico").iterdir()) == []


@pytest.mark.parametrize("status", ["queued", "executing", "waiting_user", "completed"])
def test_inbox_is_evidence_and_never_replayed(tmp_path, status):
    session(tmp_path)
    path = write(tmp_path, "ai/agent/sessions/session/inbox/input.json", {
        "id": "input", "status": status, "origin": "chat", "accepted_at": 1,
        "message": {"kind": "submit", "id": "input", "text": "retained",
                    "context": BoundContext("i", "g", "t", {}).record()}})
    before = path.read_bytes()
    report = audit(tmp_path)
    assert ("unresolved_session" in codes(report)) == (status != "completed")
    assert path.read_bytes() == before


@pytest.mark.parametrize("backend,state,verified,expected", [
    ("lsf", "completed", True, None), ("lsf", "completed", False, "unresolved_scheduler_job"),
    ("local", "completed", False, None), ("local", "cleanup_failed", False, "unresolved_local_job"),
])
def test_background_completion_requires_backend_evidence(tmp_path, backend, state, verified, expected):
    session(tmp_path)
    write(tmp_path, "ai/agent/background/records/job.json", {
        "protocol": "cad_ai_background_jobs.v1", "job_id": "job", "worker_id": "worker",
        "service_id": "service", "owner_session_id": "session", "state": state,
        "execution_backend": backend, "scheduler_verified": verified,
        "automatic_resume_allowed": False, "origin": BoundContext("i", "g", "t", {}).record()})
    report = audit(tmp_path)
    assert codes(report) == ({expected} if expected else set())


def test_identity_is_classified_for_rebinding_not_copied(tmp_path):
    from sico.service.project_identity import create_identity
    from sico.storage.project_files import registry_directory
    from sico.storage.project_lock import ProjectLock

    (tmp_path / ".cad").mkdir(mode=0o700)
    from legacy_state_fixtures import legacy_writer
    with legacy_writer():
        directory = registry_directory(tmp_path, create=True)
        with ProjectLock(directory, create=True):
            identity = create_identity(directory, "a" * 32)
    report = audit(tmp_path)
    assert not report["blockers"]
    entry = next(row for row in report["entries"] if row["schema"] == "project")
    assert entry["action"] == "identity"
    assert entry["object_id"] == identity.project_id
    row = json.loads((directory / "project.json").read_text())
    row["directory_inode"] += 1
    (directory / "project.json").write_text(json.dumps(row))
    assert "invalid_or_changed_record" in codes(audit(tmp_path))


def test_diagnostics_never_include_record_values(tmp_path):
    secret = "DO-NOT-PRINT-INLINE-CREDENTIAL"
    write(tmp_path, "ai/agent-provider.json", {"api_key": secret})
    report = audit(tmp_path)
    assert "invalid_or_changed_record" in codes(report)
    assert secret not in json.dumps(report)


def test_invalid_project_identity_is_reported_without_raising(tmp_path):
    write(tmp_path, "ai/agent/service/project.json", {
        "protocol": "sico_project_identity.v1", "project_id": "invalid",
        "created_host": "a" * 32, "directory_inode": 1,
        "directory_device": 1, "lock_inode": 1})
    assert "invalid_or_changed_record" in codes(audit(tmp_path))


def test_missing_legacy_root_and_unsafe_root_do_not_create_state(tmp_path):
    assert "legacy_root_missing" in codes(audit(tmp_path))
    assert list(tmp_path.iterdir()) == []
    (tmp_path / ".cad").symlink_to(tmp_path)
    assert "unsafe_legacy_root" in codes(audit(tmp_path))


def test_cli_rejects_apply_before_creating_anything(tmp_path):
    environment = dict(os.environ)
    environment.pop("CAD_HOME", None)
    environment["SICO_HOME"] = str(Path(__file__).resolve().parents[4])
    result = subprocess.run([sys.executable, "-m", "sico", "migration-audit",
                             "--project-dir", str(tmp_path), "--apply"],
                            capture_output=True, text=True, timeout=20, env=environment)
    assert result.returncode == 2
    assert "read-only" in result.stderr
    assert list(tmp_path.iterdir()) == []
