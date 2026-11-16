"""Migration preserves captured evidence while rejecting unresolved session work."""

import json
from pathlib import Path

import pytest

from sico.core.contracts import BoundContext, RunState
from sico.migration.audit import audit
from sico.migration.prepare import prepare
from sico.service.frontend_session import SessionToken
from sico.service.project_identity import create_identity
from sico.service.recovery_facts import read_facts
from sico.service.service_session_dto import SessionAddress
from legacy_state_fixtures import HistoricalJournal as SessionJournal
from sico.storage.project_files import registry_directory, write_record
from sico.storage.project_lock import ProjectLock
from sico.storage.session_release import NAME
from legacy_state_fixtures import save_release
from sico.storage.session_snapshot import capture, save


@pytest.fixture(autouse=True)
def isolated_observation(monkeypatch):
    observe = lambda _source: {"scope": "local", "referencing_pids": [], "unreadable_processes": 0}
    monkeypatch.setattr("sico.migration.audit.scan_local", observe)
    monkeypatch.setattr("sico.migration.prepare.scan_local", observe)


from legacy_state_fixtures import legacy_fixture


@legacy_fixture
def seed(project, *, status=None, snapshot=True, ended=False, release=True):
    (project / ".cad").mkdir(mode=0o700, exist_ok=True)
    context = BoundContext("instance", "generation", "target", {"cwd": str(project)})
    service = registry_directory(project, create=True)
    with ProjectLock(service, create=True):
        identity = create_identity(service, "a" * 32)
    address = SessionAddress(identity.project_id, "b" * 32,
                             SessionToken("session", "c" * 32))
    with SessionJournal(project, "session") as journal:
        state = RunState(context)
        journal.append("session.created", {"context": context.record()}, state)
        if snapshot:
            save(journal.directory, capture("session", context, None,
                {"PATH": str(project / ".cad/bin"), "UNRELATED_SECRET": "not-persisted"},
                {}, identity.project_id))
        if status:
            state.task = dict(id="task", status="executing", pending_calls=[])
            journal.append("task.started", {"text": "read", "context": context.record()}, state)
            if status != "executing":
                state.task["status"] = status
                journal.append("task." + status, {}, state)
        event = None
        if ended:
            event = journal.append_session("session.end_observed", dict(
                address=address.record(), operation_id="end", state="drained"), context)
        directory = journal.directory
    if event and release:
        save_release(directory, address, "end", event["sequence"])
    return directory, address, context


def codes(project):
    return {entry["code"] for entry in audit(project)["blockers"]}


def test_preparation_preserves_snapshot_and_release_without_rebinding(tmp_path):
    directory, _address, _context = seed(tmp_path, status="completed", ended=True)
    original = {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}
    facts = read_facts(tmp_path, "session")
    assert facts.resources_released and not facts.unresolved
    report = audit(tmp_path)
    assert not report["blockers"]
    result = prepare(tmp_path, expected_inventory=report["inventory_sha256"], writers_stopped=True)
    for location in ("backup", "staged"):
        copied = Path(result["transaction"]) / location / "ai/agent/sessions/session"
        for name in ("events.jsonl", "session.snapshot.json", NAME):
            assert (copied / name).read_bytes() == original[name]
    assert not (tmp_path / ".sico").exists()
    assert b"not-persisted" not in original["session.snapshot.json"]
    assert b".cad/bin" in original["session.snapshot.json"]


@pytest.mark.parametrize("status", ["executing", "needs_reconcile"])
@pytest.mark.parametrize("snapshot", [False, True])
def test_unresolved_task_without_inbox_blocks_preparation(tmp_path, status, snapshot):
    seed(tmp_path, status=status, snapshot=snapshot)
    report = audit(tmp_path)
    assert "unresolved_session" in {row["code"] for row in report["blockers"]}
    with pytest.raises(ValueError, match="blocked"):
        prepare(tmp_path, expected_inventory=report["inventory_sha256"], writers_stopped=True)
    assert not (tmp_path / ".sico-migration").exists()


def test_end_observation_without_resource_release_stays_unresolved(tmp_path):
    seed(tmp_path, ended=True, release=False)
    assert "unresolved_session" in codes(tmp_path)


@pytest.mark.parametrize("damage", ["sequence", "session", "project", "operation", "boolean"])
def test_release_must_match_original_end_event(tmp_path, damage):
    directory, _address, _context = seed(tmp_path, ended=True)
    path = directory / NAME
    row = json.loads(path.read_bytes())
    if damage == "sequence":
        row["sequence"] += 1
    elif damage == "session":
        row["address"]["session_id"] = "another"
    elif damage == "project":
        row["address"]["project_id"] = "f" * 32
    elif damage == "operation":
        row["operation_id"] = "another"
    else:
        row["version"] = True
    write_record(path, row)
    before = path.read_bytes()
    assert "invalid_or_changed_session" in codes(tmp_path)
    assert path.read_bytes() == before


def test_orphan_receipt_cannot_establish_session_completion(tmp_path):
    directory, address, _context = seed(tmp_path)
    save_release(directory, address, "end", 1)
    assert "invalid_or_changed_session" in codes(tmp_path)


def test_snapshot_identity_must_belong_to_inventoried_project(tmp_path):
    directory, _address, context = seed(tmp_path)
    save(directory, capture("session", context, None, {}, {}, "f" * 32))
    assert "invalid_or_changed_session" in codes(tmp_path)


def test_snapshot_revision_and_journal_generation_are_validated(tmp_path):
    directory, _address, _context = seed(tmp_path)
    path = directory / "session.snapshot.json"
    row = json.loads(path.read_bytes())
    row["environment"]["PATH"] = "/changed"
    write_record(path, row)
    assert "invalid_or_changed_record" in codes(tmp_path)
    other = BoundContext("instance", "other-generation", "target", {})
    save(directory, capture("session", other, None, {}, {}, row["project_id"]))
    assert "invalid_or_changed_session" in codes(tmp_path)


def test_deleting_thread_does_not_drain_unresolved_task(tmp_path):
    seed(tmp_path, status="executing")
    with SessionJournal(tmp_path, "session") as journal:
        journal.append("codex.thread.deleted", {}, journal.state)
    assert "unresolved_session" in codes(tmp_path)


def test_snapshot_without_journal_is_not_a_recoverable_session(tmp_path):
    directory, _address, _context = seed(tmp_path)
    (directory / "events.jsonl").unlink()
    assert "missing_session_journal" in codes(tmp_path)


@pytest.mark.parametrize("kind", ["session.end_observed", "session.input_abandoned", "inbox"])
def test_snapshotless_addresses_must_match_inventoried_project(tmp_path, kind):
    directory, address, context = seed(tmp_path, snapshot=False)
    wrong = dict(address.record(), project_id="f" * 32)
    if kind == "inbox":
        inbox = directory / "inbox"
        inbox.mkdir(mode=0o700)
        write_record(inbox / "input.json", dict(id="input", status="completed", origin="chat",
            accepted_at=1, service_address=wrong,
            message=dict(kind="submit", id="input", text="done", context=context.record())))
    else:
        payload = (dict(address=wrong, operation_id="end", state="drained")
                   if kind == "session.end_observed" else
                   dict(address=wrong, input_id="input", external_outcome="unconfirmed"))
        with SessionJournal(tmp_path, "session") as journal:
            event = journal.append_session(kind, payload, context)
        if kind == "session.end_observed":
            save_release(directory, SessionAddress.from_record(wrong), "end", event["sequence"])
    assert "invalid_or_changed_session" in codes(tmp_path)


@pytest.mark.parametrize("requested", [False, True])
def test_live_recovery_reads_release_only_for_observed_end(tmp_path, requested):
    directory, address, context = seed(tmp_path)
    if requested:
        with SessionJournal(tmp_path, "session") as journal:
            journal.append_session("session.end_requested", dict(
                address=address.record(), operation_id="end"), context)
    receipt = directory / NAME
    receipt.write_bytes(b"damaged receipt")
    receipt.chmod(0o600)
    facts = read_facts(tmp_path, "session")
    assert not facts.resources_released
    assert facts.unresolved == requested
    assert "invalid_or_changed_session" in codes(tmp_path)


@pytest.mark.parametrize("resolution", ["completed", "abandoned", "still_executing"])
def test_inbox_admission_uses_journal_resolution(tmp_path, resolution):
    directory, address, context = seed(tmp_path)
    inbox = directory / "inbox"
    inbox.mkdir(mode=0o700)
    path = inbox / "input.json"
    write_record(path, dict(id="input", status="queued", origin="chat", accepted_at=1,
        service_address=address.record(),
        message=dict(kind="submit", id="input", text="read", context=context.record())))
    before = path.read_bytes()
    with SessionJournal(tmp_path, "session") as journal:
        state = RunState(context)
        if resolution != "abandoned":
            state.task = dict(id="task", status="executing", pending_calls=[])
            journal.append("task.started", dict(input_id="input", text="read",
                                                 context=context.record()), state)
        if resolution == "completed":
            state.task["status"] = "completed"
            journal.append("task.completed", {}, state)
        else:
            journal.append("session.input_abandoned", dict(input_id="input",
                address=address.record(), external_outcome="unconfirmed"), state)
    report = audit(tmp_path)
    assert {row["code"] for row in report["blockers"]} == (
        {"unresolved_session"} if resolution == "still_executing" else set())
    assert path.read_bytes() == before


@pytest.mark.parametrize("observed", [False, True])
def test_deleted_session_still_requires_end_resource_release(tmp_path, observed):
    _directory, address, context = seed(tmp_path, ended=observed, release=False)
    with SessionJournal(tmp_path, "session") as journal:
        if not observed:
            journal.append_session("session.end_requested", dict(
                address=address.record(), operation_id="end"), context)
        journal.append("codex.thread.deleted", {}, journal.state)
    assert "unresolved_session" in codes(tmp_path)


@pytest.mark.parametrize("same_host", [False, True])
def test_identity_device_change_is_valid_on_same_or_different_host(tmp_path, same_host):
    from sico.service.service_discovery import local_host

    seed(tmp_path)
    path = tmp_path / ".cad/ai/agent/service/project.json"
    record = json.loads(path.read_bytes())
    record["created_host"] = local_host().host_id if same_host else "f" * 32
    record["directory_device"] += 1
    write_record(path, record)
    before = path.read_bytes()
    assert "invalid_or_changed_record" not in codes(tmp_path)
    assert path.read_bytes() == before
