"""Persistent cutover must survive interruptions without replacing any existing root."""

import fcntl
import json
import os
from pathlib import Path

import pytest

from sico.migration.activation import activate
from sico.migration.audit import audit
from sico.migration.prepare import prepare
from sico.service.project_identity import read_identity
from sico.storage.history_policy import read as read_history_policy
from sicostate import ensure
from sico.storage.migration_locations import target_for
from test_sico_migration_sessions import seed


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    observe = lambda _source: {"scope": "local", "referencing_pids": [], "unreadable_processes": 0}
    monkeypatch.setattr("sico.migration.audit.scan_local", observe)
    monkeypatch.setattr("sico.migration.prepare.scan_local", observe)
    seed(tmp_path, status="completed", ended=True)
    report = audit(tmp_path)
    assert not report["blockers"], report["blockers"]
    result = prepare(tmp_path, expected_inventory=report["inventory_sha256"], writers_stopped=True)
    return tmp_path, report["inventory_sha256"], Path(result["transaction"])


def finish(prepared):
    project, digest, _transaction = prepared
    return activate(project, expected_inventory=digest, writers_stopped=True)


def test_activation_publishes_new_identity_and_retains_source(prepared):
    project, digest, transaction = prepared
    source = project / ".cad"
    before = {p: (p.read_bytes(), p.stat().st_mode) for p in source.rglob("*") if p.is_file()}
    old = read_identity(source / "ai/agent/service")
    result = finish(prepared)
    assert result["status"] == "activated" and result["cutover"] is True
    assert result["project_id"] != old.project_id
    new = read_identity(project / ".sico/ai/agent/service")
    assert new.project_id == result["project_id"] and new.lock_inode != old.lock_inode
    new.verify(project / ".sico/ai/agent/service")
    assert before == {p: (p.read_bytes(), p.stat().st_mode) for p in before}
    policy = read_history_policy(project / ".sico/ai/agent")
    assert policy["source_project_id"] == old.project_id
    assert policy["inventory_sha256"] == digest
    assert not (transaction / "candidate").exists()
    assert not list((project / ".sico/ai/agent/sessions").rglob("*.lock"))
    assert finish(prepared) == result
    assert ensure(project / ".sico") == project / ".sico"


@pytest.mark.parametrize("phase", ["copy", "identity", "ready", "publish", "receipt"])
def test_interrupted_activation_resumes_without_duplicate_identity(prepared, monkeypatch, phase):
    import sico.migration.activation as module

    project, _digest, transaction = prepared
    called = False
    name = {"copy": "copy_entry", "identity": "create_identity", "ready": "write_record",
            "publish": "publish", "receipt": "write_record"}[phase]
    original = getattr(module, name)

    def interrupted(*args, **kwargs):
        nonlocal called
        value = original(*args, **kwargs)
        qualifies = (phase not in {"ready", "receipt"} or args[0].name == "activation.json"
                     and args[1]["status"] == ("ready" if phase == "ready" else "activated"))
        if qualifies and not called:
            called = True
            raise OSError("simulated power loss")
        return value

    monkeypatch.setattr(module, name, interrupted)
    with pytest.raises(OSError, match="power loss"):
        finish(prepared)
    assert called
    root = project / ".sico" if (project / ".sico").exists() else transaction / "candidate"
    identity = root / "ai/agent/service/project.json"
    previous = identity.read_bytes() if identity.exists() else None
    monkeypatch.setattr(module, name, original)
    result = finish(prepared)
    if previous is not None:
        assert (project / ".sico/ai/agent/service/project.json").read_bytes() == previous
    assert finish(prepared) == result


@pytest.mark.parametrize("conflict", ["directory", "symlink", "file", "race"])
def test_existing_target_is_never_replaced(prepared, monkeypatch, conflict):
    import sico.migration.activation as module

    project, _digest, _transaction = prepared
    target = project / ".sico"
    if conflict == "symlink":
        target.symlink_to(project / ".cad")
    elif conflict == "file":
        target.write_text("unrelated")
    elif conflict == "directory":
        target.mkdir(mode=0o700)
    else:
        original = module.publish
        def raced(source, destination):
            destination.mkdir(mode=0o700)
            return original(source, destination)
        monkeypatch.setattr(module, "publish", raced)
    before = target.lstat().st_ino if conflict != "race" else None
    with pytest.raises((ValueError, FileExistsError)):
        finish(prepared)
    assert target.lstat().st_ino == (before or target.lstat().st_ino)
    if conflict == "race":
        assert not list(target.iterdir())


@pytest.mark.parametrize("damage", ["backup", "staged", "inventory", "extra", "source"])
def test_damaged_evidence_never_publishes(prepared, damage):
    project, _digest, transaction = prepared
    if damage in {"backup", "staged"}:
        next((transaction / damage).rglob("events.jsonl")).write_bytes(b"changed")
    elif damage == "inventory":
        (transaction / "inventory.json").write_text("{}")
    elif damage == "source":
        next((project / ".cad").rglob("events.jsonl")).write_bytes(b"changed")
    else:
        (transaction / "staged/unregistered").write_text("extra")
    with pytest.raises(ValueError):
        finish(prepared)
    assert not (project / ".sico").exists()


def test_shutdown_assertion_and_held_migration_lock_are_required(prepared):
    project, digest, _transaction = prepared
    with pytest.raises(ValueError, match="shutdown"):
        activate(project, expected_inventory=digest)
    with (project / ".sico-migration/owner.lock").open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            finish(prepared)
    assert not (project / ".sico").exists()


def test_writer_race_before_publication_is_rejected(prepared, monkeypatch):
    import sico.migration.activation as module

    project, _digest, _transaction = prepared
    original = module.candidate
    def raced(*args):
        result = original(*args)
        next((project / ".cad").rglob("events.jsonl")).write_bytes(b"raced writer")
        return result
    monkeypatch.setattr(module, "candidate", raced)
    with pytest.raises(ValueError):
        finish(prepared)
    assert not (project / ".sico").exists()


@pytest.mark.parametrize("damage", ["receipt", "marker", "history", "missing-history", "identity",
                                   "inventory", "source", "lock", "copied-target", "receipt-symlink"])
def test_inconsistent_published_root_is_never_admitted(prepared, damage):
    import shutil

    project, _digest, transaction = prepared
    finish(prepared)
    root = project / ".sico"
    paths = {"receipt": transaction / "activation.json", "marker": root / "migration.json",
             "history": root / "ai/agent/history-migration.json",
             "identity": root / "ai/agent/service/project.json"}
    if damage in paths:
        paths[damage].write_text("{}")
    elif damage == "missing-history":
        (root / "ai/agent/history-migration.json").unlink()
    elif damage == "inventory":
        (transaction / "inventory.json").write_text("{}")
    elif damage == "source":
        (project / ".cad").rename(project / "legacy-moved")
        (project / ".cad").mkdir(mode=0o700)
    elif damage == "lock":
        path = root / "ai/agent/service/owner.lock"
        path.rename(path.with_suffix(".old"))
        path.write_bytes(b"")
        path.chmod(0o600)
    elif damage == "copied-target":
        root.rename(project / "moved")
        shutil.copytree(project / "moved", root)
    else:
        path = transaction / "activation.json"
        moved = path.with_suffix(".moved")
        path.rename(moved)
        path.symlink_to(moved)
    with pytest.raises((ValueError, OSError)):
        ensure(root)


def test_publication_without_final_receipt_is_not_runnable(prepared, monkeypatch):
    import sico.migration.activation as module

    project, _digest, _transaction = prepared
    original = module.publish
    def interrupted(*args):
        original(*args)
        raise OSError("crash after rename")
    monkeypatch.setattr(module, "publish", interrupted)
    with pytest.raises(OSError, match="crash"):
        finish(prepared)
    with pytest.raises(ValueError, match="incomplete"):
        ensure(project / ".sico")
    monkeypatch.setattr(module, "publish", original)
    finish(prepared)
    assert ensure(project / ".sico") == project / ".sico"


def test_launch_alias_retains_spelling_and_same_identity(prepared):
    project, _digest, _transaction = prepared
    finish(prepared)
    alias = project.parent / "NAS alias"
    alias.symlink_to(project, target_is_directory=True)
    assert ensure(alias / ".sico") == alias / ".sico"


def test_admission_after_device_renumbering_preserves_migration_evidence(prepared, monkeypatch):
    from types import SimpleNamespace
    import sicomigration
    from sico.service.project_service import ProjectServiceLease

    project, _digest, transaction = prepared
    result = finish(prepared)
    root = project / ".sico"
    paths = [root / "migration.json", root / "ai/agent/service/project.json",
             transaction / "activation.json", transaction / "inventory.json"]
    before = {path: path.read_bytes() for path in paths}
    original = sicomigration.directory

    def renumbered(path, **kwargs):
        info = original(path, **kwargs)
        return SimpleNamespace(st_ino=info.st_ino, st_uid=info.st_uid,
                               st_dev=info.st_dev + 1)

    monkeypatch.setattr(sicomigration, "directory", renumbered)
    assert ensure(root) == root
    with ProjectServiceLease.acquire(project) as owner:
        assert owner.project_id == result["project_id"]
    assert {path: path.read_bytes() for path in paths} == before


def test_published_inventory_maps_legacy_absolute_paths_to_new_root(prepared):
    project, _digest, transaction = prepared
    finish(prepared)
    source = project / ".cad" / "ai/agent/sessions/session/events.jsonl"
    target = project / ".sico" / "ai/agent/sessions/session/events.jsonl"
    assert target_for(project / ".sico", source) == target
    with (transaction / "inventory.json").open() as stream:
        inventory = json.load(stream)
    assert next(row for row in inventory["locations"] if row["source"] == str(source))["target"] == str(target)


def test_unprepared_activation_does_not_create_control_directory(tmp_path):
    with pytest.raises((ValueError, OSError)):
        activate(tmp_path, expected_inventory="a" * 64, writers_stopped=True)
    assert list(tmp_path.iterdir()) == []


def test_terminal_uses_activated_root_and_refuses_missing_source(prepared):
    from sicotemp import initialize

    project, _digest, _transaction = prepared
    finish(prepared)
    original = {p: p.read_bytes() for p in (project / ".cad").rglob("*") if p.is_file()}
    environment = {"SICO_TEMP_DIR": str(project / ".sico"), "TMPDIR": "/vendor/tmp"}
    assert initialize(environment) == project / ".sico/ai"
    assert original == {p: p.read_bytes() for p in original}
    (project / ".cad").rename(project / "missing-source")
    with pytest.raises(ValueError, match="activation"):
        initialize(environment)
