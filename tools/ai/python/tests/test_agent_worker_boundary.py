"""Slow storage/RPC work must not stall Qt or acknowledge undurable inputs."""

from sico.service.frontend_session import SessionToken

import threading

import pytest
from agent_binding_fixtures import ReleaseGate, ResourceBroker
from agent_gui_wait import settle, wait_until
from agent_quick_fixture import QuickInput
from agent_sessions_harness import DesktopSessions
from agent_worker_fixtures import heartbeat
from agent_worker_fixtures import window as window
from info_app_fixtures import notice_text

from sico.core.contracts import BoundContext, CircuitCallError, NeedsReconcile
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal
from sico.transport.router import SkillInstanceRouter


def test_slow_open_is_background_and_late_receipt_cannot_override_selection(window, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = window.sessions.open
    owner = threading.get_ident()

    def slow_open(session_id):
        assert threading.get_ident() != owner
        entered.set()
        assert release.wait(5)
        return original(session_id)

    monkeypatch.setattr(window.sessions, "open", slow_open)
    window.page.activate_session("history")
    try:
        assert entered.wait(5)
        assert window.controller.session_id == "initial"
        heartbeat(window)
        window.page.activate_session("initial")
        assert window.input.isEnabled()
    finally:
        release.set()
    settle(window)
    assert window.controller.session_id == "initial"
    assert "history" in window.sessions.controllers


def test_slow_rename_and_attachment_failure_leave_qt_responsive_and_draft_intact(
    window, monkeypatch,
):
    controller = window.controller
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()

    def slow_rename(name):
        assert threading.get_ident() != owner
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(controller.loop, "set_thread_name", slow_rename, raising=False)
    window.actions.run_command("rename", "new name")
    try:
        assert entered.wait(5)
        heartbeat(window)
        window.binding.poll()
        assert controller.updates.read_updates(blocking=False)["snapshot"] is None
    finally:
        release.set()
    settle(window)

    def fail_save(record):
        assert threading.get_ident() != owner
        raise OSError("disk full")

    monkeypatch.setattr(controller.inbox, "_save", fail_save)
    window.input.setPlainText("keep the unsaved draft")
    window.actions.submit()
    settle(window)
    assert window.input.toPlainText() == "keep the unsaved draft"
    assert window.send.isEnabled()
    assert "disk full" in notice_text(window)
    assert "disk full" not in window.status.text()
    assert not controller.inbox.pending
    assert not controller.loop.provider.requests


def test_router_cancel_receipt_runs_off_qt_and_keeps_owner_and_target(window):
    entered, release = threading.Event(), threading.Event()
    controller = window.controller
    owner = threading.get_ident()
    request_id = "a" * 32
    results = []

    class Broker:
        def cancel_request(self, context, incoming, *, session_id):
            assert threading.get_ident() != owner
            assert context.record() == controller.current.record()
            assert incoming == request_id and session_id == controller.session_id
            entered.set()
            assert release.wait(5)
            return "running_unknown"

    controller.bindings.attach_targets(Broker())
    window.actions.run_command("cancel_router_request", request_id, context=controller.current,
                       success=results.append)
    try:
        assert entered.wait(2)
        heartbeat(window)
        assert not results
    finally:
        release.set()
    settle(window)
    assert results == ["running_unknown"]
    assert not controller.paused and not controller._cancel.is_set()


def test_router_snapshot_waits_only_in_metadata_worker_and_qt_reads_cache(window):
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()
    controller = window.controller
    router = SkillInstanceRouter("i", "g")

    class Broker:
        def router_snapshot(self, context):
            assert threading.get_ident() != owner
            assert context == controller.current
            entered.set()
            assert release.wait(5)
            return router.snapshot()

    controller.bindings.attach_targets(Broker())
    try:
        assert entered.wait(2)
        heartbeat(window)
        window.binding.poll()
        assert controller.updates.read_updates(blocking=False)["snapshot"]["router"] is None
    finally:
        release.set()
    wait_until(lambda: (window.presentation.state or {}).get("router") is not None)
    assert window.presentation.state["router"]["state"] == "idle"
    version = window.presentation.state["version"]
    assert controller.updates.read_updates(since_version=version, blocking=False)["snapshot"] is None


def test_router_status_renders_owner_and_live_queue_without_changing_history_preview(window):
    window.binding.timer.stop()
    state = window.controller.updates.read_updates()["snapshot"]
    request = {
        "request_id": "b" * 32, "session_id": "initial", "task_id": "task",
        "target_id": "initial", "state": "queued", "queue_position": 2,
    }
    owner = {
        "request_id": "a" * 32, "session_id": "other-session", "task_id": "other-task",
        "target_id": "other-target", "state": "running", "started_at": 1_750_000_000,
    }
    state.update(
        busy=True, task={"id": "task", "status": "executing"},
        requests=[{"id": "input", "task_id": "task", "text": "Read the current design",
                   "status": "executing", "context": state["context"]}],
        router={"instance_id": "i", "generation": "g", "state": "running", "queued": 2,
                "owner": owner, "requests": [request]},
    )
    window.renderer.update_session(state)
    assert "第 2 位" in window.status.text()
    assert window.task_panel.topLevelItemCount() == 1
    assert "第2位" in window.task_panel.topLevelItem(0).text(0)
    assert "other-session" in window.status.toolTip() and "a" * 32 in window.status.toolTip()
    assert "开始时间" in window.status.toolTip() and "b" * 32 in window.status.toolTip()

    state["router"].update(state="blocked_unknown", requests=[])
    owner["state"] = "timed_out_unknown"
    window.renderer.update_session(state)
    assert "结果待确认" in window.status.text()
    assert "第2位" not in window.task_panel.topLevelItem(0).text(0)

    state["router"].update(state="idle", owner=None)
    window.renderer.update_session(state)
    assert "结果待确认" not in window.status.text()
    assert "other-session" not in window.status.toolTip()
    state["router"].update(state="router_unavailable")
    window.renderer.update_session(state)
    assert "未连接" in window.status.text()

    state["router"].update(state="running", owner=owner, requests=[request])
    window.renderer.update_session(state)
    assert "第2位" in window.task_panel.topLevelItem(0).text(0)
    window.page.preview_session("history")
    assert window.task_panel.topLevelItemCount() == 0
    settle(window)
    window.renderer.update_session(state)
    assert "只读" in window.status.text()
    assert window.status.toolTip() == "" and not window.send.isEnabled()


@pytest.mark.parametrize("task_id", [None, ""])
def test_router_request_without_task_id_does_not_label_unstarted_input(window, task_id):
    window.binding.timer.stop()
    state = window.controller.updates.read_updates()["snapshot"]
    state.update(
        busy=True, task={"activity": {"label": "Preparing context"}},
        requests=[{"id": "input", "task_id": task_id, "text": "Next task", "status": "queued",
                   "context": state["context"]}],
        router={"instance_id": "i", "generation": "g", "state": "running", "queued": 0,
                "owner": None, "requests": [{"request_id": "a" * 32, "task_id": task_id,
                                              "state": "running"}]},
    )
    window.renderer.update_session(state)
    assert window.status.text() == "Preparing context"
    assert "Virtuoso" not in window.task_panel.topLevelItem(0).text(0)


def test_quick_accept_notice_waits_for_durable_queue_write(window, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()
    notices = []

    class Broker:
        def register_target(self, context):
            assert threading.get_ident() != owner

        def release_target(self, context):
            assert threading.get_ident() != owner

    window.quick_input = QuickInput(window, Broker(), lambda kind, **data: notices.append(kind))
    window.controller.cancel()
    original = window.controller.inbox._save

    def slow_save(record):
        assert threading.get_ident() != owner
        entered.set()
        assert release.wait(5)
        return original(record)

    monkeypatch.setattr(window.controller.inbox, "_save", slow_save)
    window.quick_input.accept({"kind": "submit", "id": "quick", "text": "read source",
                               "context": BoundContext("i", "g", "quick", {}).record()})
    try:
        assert entered.wait(5)
        heartbeat(window)
        assert "accepted" not in notices
        assert not (window.controller.inbox.directory / "quick.json").exists()
    finally:
        release.set()
    settle(window, tasks=False)
    assert notices.count("accepted") == 1
    assert (window.controller.inbox.directory / "quick.json").exists()


def test_submission_reserves_displayed_target_while_worker_queue_waits(tmp_path, monkeypatch):
    first = BoundContext("i", "g", "first", {})
    next_context = BoundContext("i", "g", "next", {})
    entered, release = threading.Event(), threading.Event()
    with SessionJournal(tmp_path, "reserved") as journal:
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, first)
        controller = SessionController(loop)
        sessions = DesktopSessions(controller)

        def slow_name(name):
            entered.set()
            assert release.wait(5)

        monkeypatch.setattr(controller.loop, "set_thread_name", slow_name, raising=False)
        sessions.worker.command(SessionToken(controller.session_id, controller.runtime_id), "rename", "name")
        try:
            assert entered.wait(5)
            future = sessions.worker.command(SessionToken(controller.session_id, controller.runtime_id), "submit", "keep source", context=first)
            controller.current = next_context
            controller.bindings.displayed = next_context
            controller.paused = True
            release.set()
            future.result(5)
            assert controller.inbox.pending[0]["message"]["context"]["target_id"] == "first"
            assert not controller.bindings._submissions
        finally:
            release.set()
            sessions.close()
            assert sessions.wait(5)


@pytest.mark.parametrize("outcome", ["success", "refused", "disconnected", "close"])
def test_release_rpc_never_blocks_qt_submit_or_heartbeat(window, monkeypatch, outcome):
    controller = window.controller
    window.sessions.metadata.close()
    assert window.sessions.metadata.wait(5)
    broker = ResourceBroker(controller)
    original = broker.release_binding_resource

    def respond(*args):
        if outcome == "refused":
            raise CircuitCallError("binding_busy", "resource is busy",
                                   data={"operation_dispatched": False})
        if outcome == "disconnected":
            raise NeedsReconcile("bridge disconnected; inspect receipts")
        return original(*args)

    gate = ReleaseGate(respond)
    monkeypatch.setattr(broker, "release_binding_resource", gate)
    future = window.actions.run_command("release_binding_resource", "resource_0")
    try:
        assert gate.entered.wait(5)
        window.input.setPlainText("Retain the resource-dependent draft")
        window.input._text_attachment = "Evidence attached to the draft"
        draft = window.input.draft()
        window.actions.submit()
        assert not gate.returned.is_set() and not future.done()
        assert "正在释放" in notice_text(window)
        assert "正在释放" not in window.status.text()
        assert window.input.draft() == draft and window.send.isEnabled()
        assert not window.submissions and not controller.bindings._submissions
        assert not controller.inbox.pending and not controller.loop.provider.requests
        heartbeat(window)
        if outcome == "close":
            window.close()
            assert controller._shutdown.is_set()
            heartbeat(window)
        assert not gate.returned.is_set()
    finally:
        gate.release.set()
    if outcome == "close":
        assert window.sessions.wait(5)
        assert controller.bindings._resource_release is None
    else:
        settle(window)
        assert window.input.draft() == draft and not controller.bindings._submissions
        if outcome == "disconnected":
            assert isinstance(future.exception(), NeedsReconcile)
            assert controller.bindings._resource_release.unknown
            with pytest.raises(ValueError, match="待核对"):
                controller.bindings.reserve_submission(controller.current)
        else:
            assert controller.bindings._resource_release is None
            controller.paused = True
            window.actions.submit()
            settle(window, tasks=False)
            assert not window.input.toPlainText()
            assert controller.inbox.pending[0]["message"]["context"] == controller.current.record()
    assert not gate.expired and not controller.loop.provider.requests


def test_full_command_queue_preserves_draft_and_cleans_cancelled_reservations(window, monkeypatch):
    controller, worker = window.controller, window.sessions.worker
    gate = ReleaseGate(lambda name: None)
    monkeypatch.setattr(controller.loop, "set_thread_name", gate, raising=False)
    worker.command(SessionToken(controller.session_id, controller.runtime_id), "rename", "hold queue")
    try:
        assert gate.entered.wait(5)
        pending = [worker.command(SessionToken(controller.session_id, controller.runtime_id), "submit", "queued", context=controller.current)
                   for _ in range(worker._queue.maxsize)]
        window.input.setPlainText("Keep the overflow draft")
        window.actions.submit()
        assert "queue is full" in notice_text(window)
        assert "queue is full" not in window.status.text()
        assert window.input.toPlainText() == "Keep the overflow draft"
        assert window.send.isEnabled() and not window.submissions
        assert len(controller.bindings._submissions) == len(pending)
        window.sessions.close()
        assert all(future.cancelled() for future in pending)
        assert not controller.bindings._submissions
        heartbeat(window)
        assert not gate.returned.is_set()
    finally:
        gate.release.set()
    assert window.sessions.wait(5)
    assert not controller.inbox.pending and not controller.loop.provider.requests


def test_close_during_durable_submit_does_not_launch_task(window, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    controller = window.controller
    original = controller.inbox._save

    def slow_save(record):
        entered.set()
        assert release.wait(5)
        return original(record)

    monkeypatch.setattr(controller.inbox, "_save", slow_save)
    window.input.setPlainText("save without starting after shutdown")
    window.actions.submit()
    try:
        assert entered.wait(5)
        window.sessions.close()
        assert controller._shutdown.is_set()
        heartbeat(window)
    finally:
        release.set()
    assert window.sessions.wait(5)
    assert not controller.loop.provider.requests
    assert not any(event["kind"] == "task.started" for event in controller.journal.events())
    assert all(row["status"] == "not_started" for row in controller.history.snapshot())


def test_migrated_ui_entrypoints_do_not_reintroduce_storage_or_controller_commands():
    from agent_boundary_guard import production_violations

    assert not production_violations("assistant-ui-backend")
