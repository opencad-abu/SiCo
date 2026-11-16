"""Recovery inspection and ownership boundaries without a background scan thread."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from session_recovery_boundary_fixtures import (
    PROJECT,
    SERVICE,
    VERSION,
    Lock,
    Owner,
    facts,
    install_facts,
    params,
    recovery,
)
from sico.service import session_recovery
from sico.service.session_recovery import SessionRecovery
from sico.transport.framing import ProtocolError


def test_scan_marks_damage_and_unresolved_sessions_but_honours_retirement(
        tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    root.mkdir()
    for name in ("one", "removed", "retired", "broken", "clean"):
        (root / name).mkdir()
    value = recovery(tmp_path)
    value._retired.add("retired")
    monkeypatch.setattr(session_recovery, "agent_root", lambda _project: tmp_path)
    monkeypatch.setattr(session_recovery, "removed",
                        lambda _root, name: name == "removed")

    def read(_project, name):
        if name == "broken":
            raise ValueError("damaged")
        return SimpleNamespace(unresolved=name in {"one", "retired"})

    monkeypatch.setattr(session_recovery, "read_facts", read)
    value._scan()
    assert value._unresolved == frozenset({"one", "broken"})
    assert value._scanning is False


def test_scan_handles_missing_root_closed_scan_and_unverified_failure(
        tmp_path, monkeypatch):
    value = recovery(tmp_path)
    monkeypatch.setattr(session_recovery, "agent_root", lambda _project: tmp_path)
    value._scan()
    assert value._unresolved == frozenset() and value._scanning is False

    root = tmp_path / "sessions"
    root.mkdir()
    (root / "one").mkdir()
    value = recovery(tmp_path)
    value._closed.set()
    value._unresolved = frozenset({"old"})
    monkeypatch.setattr(session_recovery, "agent_root", lambda _project: tmp_path)
    value._scan()
    assert value._unresolved == frozenset({"old"})

    value = recovery(tmp_path)
    monkeypatch.setattr(session_recovery, "agent_root",
                        lambda _project: (_ for _ in ()).throw(OSError("gone")))
    value._scan()
    assert value._unresolved == frozenset({"<unverified>"})


def test_pending_and_retired_are_read_only_projections(tmp_path):
    value = recovery(tmp_path)
    value._unresolved = frozenset({"one", "two"})
    value.owner.controllers["one"] = object()
    assert value.pending == 1
    value._scanning = True
    assert value.pending == 2
    value.retired("two")
    assert value.pending == 1 and value._unresolved == frozenset({"one"})
    value._scanning = False
    assert value.pending == 0


def test_facts_reads_without_controller_and_rejects_foreign_project(monkeypatch):
    value = recovery()
    calls = install_facts(monkeypatch, facts(snapshot={"project_id": PROJECT}))
    assert value._facts("old", "op").snapshot["project_id"] == PROJECT
    assert calls == [("old", "op")]

    install_facts(monkeypatch, facts(snapshot={"project_id": "d" * 32}))
    with pytest.raises(ProtocolError, match="another project"):
        value._facts("old")

    install_facts(monkeypatch, facts(archived=True, snapshot={"project_id": "d" * 32}))
    assert value._facts("old").archived is True


def test_facts_locks_the_controller_and_journal_before_reading(monkeypatch):
    value = recovery()
    controller = SimpleNamespace(
        _lock=Lock(), journal=SimpleNamespace(_events_lock=Lock()))
    value.owner.controllers["old"] = controller
    install_facts(monkeypatch, facts(snapshot={"project_id": PROJECT}))
    value._facts("old")
    assert controller._lock.entered == 1
    assert controller.journal._events_lock.entered == 1


@pytest.mark.parametrize("case,expected", [
    ("archived", "archived_readonly"),
    ("live", "live"),
    ("deleted", "ended"),
    ("unreleased", "ended"),
    ("legacy", "legacy_readonly"),
    ("credentials", "waiting_credentials"),
    ("ready", "ready"),
])
def test_view_names_each_recovery_state(monkeypatch, case, expected):
    value = recovery()
    controller = SimpleNamespace()
    monkeypatch.setattr(session_recovery, "session_record",
                        lambda _controller, _descriptor: {"runtime": "controller"})
    overrides = {
        "archived": {"archived": True},
        "live": {"deleted": True},
        "deleted": {"deleted": True},
        "unreleased": {"ends": ({"kind": "session.end_requested"},)},
        "legacy": {"snapshot": None},
        "credentials": {"snapshot": {
            "provider_config": {"api_key_env": "MODEL_KEY"}}},
        "ready": {"snapshot": {}},
    }[case]
    view = value._view("old", facts(**overrides),
                       controller if case == "live" else None)
    assert view["state"] == expected
    if case == "live":
        assert view["session"] == {"runtime": "controller"}
    else:
        assert view["session"] is None
    if case == "credentials":
        assert view["credential"] == "MODEL_KEY"


def test_view_selects_a_matching_operation_and_task_fields(monkeypatch):
    value = recovery()
    operation = {"operation_id": "op", "version": VERSION}
    monkeypatch.setattr(session_recovery, "session_record",
                        lambda _controller, _descriptor: None)
    view = value._view(
        "old",
        facts(task={"id": "task", "status": "executing"},
              operations=(operation, {"operation_id": "other"})),
        None,
        "op",
    )
    assert view["operation"] is operation
    assert (view["task_id"], view["task_status"]) == ("task", "executing")
    assert view["inputs"] == ()
    assert value._view("old", facts(operations=(operation,)), None, "missing")[
        "operation"] is None
    assert value._view("old", facts(archived=True, operations=(operation,)),
                       None, "op")["operation"] is None


def test_inspect_uses_one_mutation_scope_and_delegates_to_view(monkeypatch):
    value = recovery()
    controller = object()
    value.owner.controllers["old"] = controller
    seen = []
    monkeypatch.setattr(value, "_facts", lambda session, operation: "facts")
    monkeypatch.setattr(value, "_view",
                        lambda session, durable, current, operation:
                        seen.append((session, durable, current, operation)) or "view")
    assert value.inspect("old", "op") == "view"
    assert seen == [("old", "facts", controller, "op")]
    assert value.inspect("old") == "view"
    assert seen[-1] == ("old", "facts", controller, None)


def test_constructor_starts_one_scan_and_owns_cleanup(monkeypatch):
    owner, descriptor = Owner("project"), SimpleNamespace(
        project_id=PROJECT, service_id=SERVICE)
    started = []
    cleaned = []

    class Thread:
        def __init__(self, *, target, name, daemon):
            self.target, self.name, self.daemon = target, name, daemon
            self.joined = 0

        def start(self):
            started.append(self)

        def join(self):
            self.joined += 1

    class Cleanup:
        def __init__(self, callback, name):
            cleaned.append((callback, name))

        def start(self):
            return SimpleNamespace(wait=lambda _timeout: "closed")

    monkeypatch.setattr(session_recovery.threading, "Thread", Thread)
    monkeypatch.setattr(session_recovery, "CleanupTask", Cleanup)
    value = SessionRecovery(owner, descriptor)
    assert started == [value._thread]
    assert value._thread.name == "copilot-recovery" and value._thread.daemon is True
    assert cleaned == [(value._join, "copilot-recovery-close")]
    assert value.pending == 1
    assert value.close(2) == "closed"


def test_continue_session_rejects_missing_or_deleted_facts(monkeypatch):
    value = recovery()
    value._facts = lambda *_args: facts(snapshot=None)
    with pytest.raises(ValueError, match="已删除或缺少配置"):
        value.continue_session(object(), "op", "old")
    value._facts = lambda *_args: facts(deleted=True)
    with pytest.raises(ValueError, match="已删除或缺少配置"):
        value.continue_session(object(), "op", "old")


def test_continue_session_opens_chat_and_releases_the_captured_runtime(monkeypatch):
    value = recovery()
    durable = facts()
    value._facts = lambda *_args: durable
    seen = {}
    controller = SimpleNamespace(
        _lock=threading.Lock(),
        recovery=SimpleNamespace(continue_chat=lambda row: seen.setdefault("facts", row)))

    def open_(_connection, operation_id, key, passed, **kwargs):
        seen.update(operation=operation_id, key=key, passed=passed, **kwargs)
        return controller

    monkeypatch.setattr(value, "_open", open_)
    assert value.continue_session(object(), "op", "old", preferred="x") is controller
    assert seen["operation"] == "op" and seen["key"] == "old"
    assert seen["continue_chat"] is True and seen["preferred"] == "x"
    assert seen["facts"] is durable


def test_restore_rejects_closed_archived_and_changed_evidence(monkeypatch):
    value = recovery()
    value._facts = lambda *_args: facts()
    value._closed.set()
    with pytest.raises(ValueError, match="已关闭"):
        value.restore(object(), "op", params())
    value._closed.clear()
    value._facts = lambda *_args: facts(archived=True)
    with pytest.raises(ValueError, match="迁移历史"):
        value.restore(object(), "op", params())
    value._facts = lambda *_args: facts(version="d" * 64)
    with pytest.raises(ValueError, match="证据已变化"):
        value.restore(object(), "op", params())


def test_restore_returns_existing_operation_or_refuses_live_controller():
    value = recovery()
    operation = {"operation_id": "op", "version": VERSION}
    value._facts = lambda *_args: facts(operations=(operation,))
    view = value.restore(object(), "op", params())
    assert view["operation"] == operation

    value._facts = lambda *_args: facts()
    value.owner.controllers["old"] = object()
    with pytest.raises(ValueError, match="只读关联"):
        value.restore(object(), "op", params())

    operation = {"operation_id": "op", "version": "d" * 64}
    value.owner.controllers.clear()
    value._facts = lambda *_args: facts(operations=(operation,))
    with pytest.raises(ValueError, match="恢复操作 ID"):
        value.restore(object(), "op", params())


@pytest.mark.parametrize("overrides,message", [
    ({"snapshot": None}, "已删除或缺少配置"),
    ({"deleted": True}, "已删除或缺少配置"),
    ({"ends": ({"kind": "session.end_requested"},)}, "结束操作"),
])
def test_restore_refuses_unrecoverable_sessions(overrides, message):
    value = recovery()
    value._facts = lambda *_args: facts(**overrides)
    with pytest.raises(ValueError, match=message):
        value.restore(object(), "op", params())


def test_restore_requires_the_captured_credential_and_can_wait(monkeypatch):
    value = recovery()
    value._facts = lambda *_args: facts(
        snapshot={"provider_config": {"api_key_env": "MODEL_KEY"},
                  "environment": {}, "bridge_identity": {}, "background": None})
    with pytest.raises(ProtocolError, match="captured credential"):
        value.restore(object(), "op", params(credentials={"OTHER": "secret"}))
    waiting = value.restore(object(), "op", params())
    assert waiting["state"] == "waiting_credentials"
    assert waiting["credential"] == "MODEL_KEY"
    assert not value.owner.opened


def test_restore_opens_once_and_reports_the_live_result(monkeypatch):
    value = recovery()
    durable = facts()
    value._facts = lambda *_args: durable
    controller = SimpleNamespace()
    monkeypatch.setattr(session_recovery, "session_record",
                        lambda _controller, _descriptor: {"runtime": "new"})

    def open_(_connection, operation_id, key, passed, credentials=None):
        assert (operation_id, key, passed, credentials) == (
            "op", "old", durable, {})
        return controller

    monkeypatch.setattr(value, "_open", open_)
    view = value.restore(object(), "op", params())
    assert view["state"] == "live" and view["session"] == {"runtime": "new"}
    assert view["operation"] is None
