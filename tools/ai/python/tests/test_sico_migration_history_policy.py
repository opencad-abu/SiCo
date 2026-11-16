"""Migrated snapshots remain inspectable but can never construct an execution runtime."""

import hashlib
import json
import os
from contextlib import ExitStack
from pathlib import Path

import pytest

from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from agent_service_recovery_fixtures import client, restore, seed
from sico.migration.activation import activate
from sico.migration.audit import audit
from sico.migration.prepare import prepare
from sico.service.project_sessions import ProjectSessionOwner
from sico.service.recovery_facts import read_facts
from sico.storage.history import SessionReader
from sico.storage.history_policy import NAME, read
from sico.storage.journal import SessionJournal
from legacy_state_fixtures import legacy_writer


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    observe = lambda _source: {"scope": "local", "referencing_pids": [], "unreadable_processes": 0}
    monkeypatch.setattr("sico.migration.audit.scan_local", observe)
    monkeypatch.setattr("sico.migration.prepare.scan_local", observe)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / ".cad").mkdir(mode=0o700)
    with legacy_writer():
        context, address = seed(legacy, monkeypatch,
            config={"provider": "openai", "base_url": "https://example.test/v1",
                    "model": "old-model", "api_key_env": "OLD_MODEL_KEY"},
            environment={"PATH": "/legacy/bin", "SICO_PYTHON": "/legacy/python"})
        with SessionJournal(legacy, "old") as journal:
            journal.append_session("session.created", {"context": context.record()}, context)
    report = audit(legacy)
    assert not report["blockers"], report["blockers"]
    prepare(legacy, expected_inventory=report["inventory_sha256"], writers_stopped=True)
    activate(legacy, expected_inventory=report["inventory_sha256"], writers_stopped=True)
    return legacy, context, address, legacy, report


def test_prepared_policy_binds_source_hashes_without_editing_original(migrated):
    project, _context, address, legacy, report = migrated
    root = project / ".sico/ai/agent"
    policy = read(root)
    assert policy["inventory_sha256"] == report["inventory_sha256"]
    assert policy["source_project_id"] == address["project_id"]
    for name, field in (("events.jsonl", "journal_sha256"),
                        ("session.snapshot.json", "snapshot_sha256")):
        original = (legacy / ".cad/ai/agent/sessions/old" / name).read_bytes()
        assert (root / "sessions/old" / name).read_bytes() == original
        assert policy["sessions"]["old"][field] == hashlib.sha256(original).hexdigest()
    assert not (legacy / ".cad/ai/agent" / NAME).exists()
    assert SessionReader(root, "old").events()
    assert read_facts(project, "old").archived


def test_low_level_writer_and_owner_reject_before_provider_or_bridge(migrated, monkeypatch):
    project, context, _address, _legacy, _report = migrated
    directory = project / ".sico/ai/agent/sessions/old"
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    def forbidden(*_args, **_kwargs):
        pytest.fail("Migration history attempted to construct a runtime")
    monkeypatch.setattr("sico.service.project_sessions.provider_from_config", forbidden)
    with pytest.raises(ValueError, match="迁移历史"), SessionJournal(project, "old"):
        pass
    with ProjectSessionOwner(project, environment={}, bridge_factory=forbidden) as owner:
        with pytest.raises(ValueError, match="迁移历史"):
            owner.open("old", {}, context)
        assert not owner.controllers
    assert before == {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    assert not (directory / "writer.lock").exists()


def test_service_history_survives_new_project_identity_but_restore_is_forbidden(migrated, monkeypatch):
    project, _context, address, _legacy, _report = migrated
    with business_service(project, monkeypatch) as service, ExitStack() as stack:
        assert service.descriptor.project_id != address["project_id"]
        api = client(stack, service, project)
        view = api.recovery.inspect("old").result(5)
        assert view["state"] == "archived_readonly" and view["credential"] is None
        assert not view["inputs"] and view["session"] is None and view["operation"] is None
        history = api.recovery.open_history("old").result(5)
        assert history.session.runtime_id is None
        with pytest.raises(ValueError, match="迁移历史"):
            restore(api, view)
        wait_until(lambda: not service.dispatch.frontend.recovery._scanning)
        assert service.dispatch.frontend.recovery.pending == 0
        assert not service.owner.controllers and not service.provider.seen


@pytest.mark.parametrize("damage", ["journal", "snapshot", "extra-field", "symlink", "hardlink", "mode"])
def test_policy_damage_never_falls_back_to_recoverable_history(migrated, damage):
    project, _context, _address, _legacy, _report = migrated
    root = project / ".sico/ai/agent"
    path = root / NAME
    if damage in {"journal", "snapshot"}:
        name = "events.jsonl" if damage == "journal" else "session.snapshot.json"
        with (root / "sessions/old" / name).open("ab") as stream:
            stream.write(b" ")
    elif damage == "extra-field":
        value = json.loads(path.read_bytes())
        value["recoverable"] = True
        path.write_text(json.dumps(value))
    elif damage == "symlink":
        moved = path.with_suffix(".moved")
        path.rename(moved)
        path.symlink_to(moved)
    elif damage == "hardlink":
        os.link(path, root / "extra-link")
    else:
        path.chmod(0o644)
    with pytest.raises((ValueError, OSError)):
        SessionReader(root, "old")
    with pytest.raises((ValueError, OSError)), SessionJournal(project, "old"):
        pass


def test_new_session_is_not_marked_as_archived(migrated):
    project, context, _address, _legacy, _report = migrated
    with SessionJournal(project, "new") as journal:
        journal.append_session("session.created", {"context": context.record()}, context)
    assert not read_facts(project, "new").archived


def test_migrated_history_dialog_has_no_restore_or_credential_controls(migrated, monkeypatch):
    from PyQt5.QtWidgets import QLabel, QLineEdit, QPushButton
    from test_agent_service_recovery_gui import window_for

    monkeypatch.delenv("CAD_HOME", raising=False)
    monkeypatch.setenv("SICO_HOME", str(Path(__file__).resolve().parents[4]))

    project, _context, _address, _legacy, _report = migrated
    with business_service(project, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, project)
        frontend = api.recovery.open_history("old").result(5)
        window = window_for(stack, api, frontend.session)
        panel = window.service_connection.recovery
        panel.inspect()
        wait_until(lambda: panel.dialog is not None)
        assert not panel.dialog.findChildren(QLineEdit)
        assert all(button.text() != "恢复为暂停会话"
                   for button in panel.dialog.findChildren(QPushButton))
        assert any("迁移历史" in label.text() for label in panel.dialog.findChildren(QLabel))
        assert not service.owner.controllers


def test_retry_refuses_modified_generated_policy(migrated):
    _project, _context, _address, legacy, report = migrated
    control = legacy / ".sico-migration" / report["inventory_sha256"]
    path = control / "staged/ai/agent" / NAME
    path.write_text("{}")
    with pytest.raises(ValueError, match="differs"):
        prepare(legacy, expected_inventory=report["inventory_sha256"], writers_stopped=True)
