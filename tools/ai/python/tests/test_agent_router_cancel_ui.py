"""Queue cancellation stays asynchronous and scoped to its originating live view."""

import os
import threading
from concurrent.futures import Future

import pytest
from agent_gui_wait import settle, wait_until
from info_app_fixtures import notice_text

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.storage.journal import SessionJournal

REQUEST_ID = "a" * 32


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    context = BoundContext("instance", "generation", "target", {})
    with SessionJournal(tmp_path, "history") as journal:
        PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, context)._event(
            "session.created", {},
        )
    with SessionJournal(tmp_path, "initial") as journal:
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, context)
        loop._event("session.created", {})
        widget = AssistantWindow(loop)
        try:
            settle(widget)
            widget.binding.timer.stop()
            state = widget.controller.updates.read_updates()["snapshot"]
            state.update(
                busy=True, task={"id": "task", "status": "executing"},
                requests=[{"id": "input", "task_id": "task", "text": "Read design",
                           "status": "executing", "context": context.record()}],
                router={
                    "instance_id": "instance", "generation": "generation", "target_id": "target",
                    "router_id": "router", "state": "running", "queued": 1, "owner": None,
                    "requests": [{"request_id": REQUEST_ID, "session_id": "initial",
                                  "target_id": "target", "task_id": "task", "tool_call_id": "tool",
                                  "state": "queued", "queue_position": 1}],
                },
            )
            widget.renderer.update_session(state)
            widget.resize(960, 640)
            widget.show()
            widget.show_panel("tasks")
            app.processEvents()
            yield widget
        finally:
            widget.sessions.close()
            assert widget.sessions.wait(5)
            widget.close()
            widget.deleteLater()
            app.processEvents()


def menu_actions(window):
    panel = window.task_panel
    position = panel.visualItemRect(panel.topLevelItem(0)).center()
    panel.customContextMenuRequested.emit(position)
    return panel._cancel_menu[0], panel._cancel_menu[2]


def deferred_cancel(window, monkeypatch):
    calls, future = [], Future()

    def command(controller, name, *args, **kwargs):
        calls.append((controller, name, args, kwargs))
        return future

    monkeypatch.setattr(window.sessions.worker, "command", command)
    return calls, future


def test_menu_cancel_runs_off_qt_without_pausing_task_or_blocking_input(window):
    controller, owner = window.controller, threading.get_ident()
    entered, release, calls, ticks = threading.Event(), threading.Event(), [], []

    class Broker:
        def cancel_request(self, context, request_id, *, session_id):
            assert threading.get_ident() != owner
            calls.append((context, request_id, session_id))
            entered.set()
            assert release.wait(5)
            return "cancel_requested"

    controller.bindings.attach_targets(Broker())
    scope = window.router_actions.router_cancel_scope()
    menu, actions = menu_actions(window)
    assert actions[REQUEST_ID].isEnabled()
    timer = QTimer(window)
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    try:
        QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(actions[REQUEST_ID]).center())
        assert entered.wait(2)
        wait_until(lambda: len(ticks) >= 3, timeout=2)
        window.input.setFocus()
        QTest.keyClicks(window.input, "draft while cancelling")
        assert window.input.toPlainText() == "draft while cancelling"
        window.router_actions.cancel_router_request(REQUEST_ID, scope)
        _, pending_actions = menu_actions(window)
        assert not pending_actions[REQUEST_ID].isEnabled()
        assert len(calls) == 1
    finally:
        timer.stop()
        release.set()
    wait_until(lambda: not window.commands.pending)
    assert calls == [(controller.current, REQUEST_ID, controller.session_id)]
    assert "已取消" in notice_text(window)
    assert not controller.paused and not controller._cancel.is_set()
    assert not pending_actions[REQUEST_ID].isEnabled()
    window.router_actions.cancel_router_request(REQUEST_ID, scope)
    assert len(calls) == 1
    state = window.presentation.state
    state["router"].update(requests=[], queued=0)
    window.renderer.update_session(state)
    assert not window.router_actions._router_cancellations
    assert state["requests"][0]["status"] == "executing"


@pytest.mark.parametrize("receipt, message", [
    ("cancel_requested", "已取消"), ("running", "已开始执行"),
    ("running_unknown", "执行结果待确认"), ("not_owner", "不属于"),
    ("not_found", "已离开队列"), ("router_unavailable", "连接已失效"),
])
def test_receipt_explains_race_without_rewriting_worker_state(
    window, monkeypatch, receipt, message,
):
    calls, future = deferred_cancel(window, monkeypatch)
    menu, actions = menu_actions(window)
    actions[REQUEST_ID].trigger()
    assert calls == [(window.page.session, "cancel_router_request", (REQUEST_ID,),
                      {"context": window.presentation.displayed_context})]
    future.set_result(receipt)
    window.commands.poll()
    window.renderer.refresh_status()
    assert message in notice_text(window)
    assert not actions[REQUEST_ID].isEnabled()
    assert window.presentation.state["router"]["requests"][0]["state"] == "queued"
    assert not window.controller.paused and not window.controller._cancel.is_set()
    menu.close()


@pytest.mark.parametrize("receipt", ["success", "failure"])
@pytest.mark.parametrize("change", ["preview", "session", "target", "generation", "router"])
def test_late_receipt_cannot_overwrite_another_view(window, monkeypatch, receipt, change):
    calls, future = deferred_cancel(window, monkeypatch)
    scope = window.router_actions.router_cancel_scope()
    menu, actions = menu_actions(window)
    actions[REQUEST_ID].trigger()
    if change == "preview":
        window.page.preview_session("history")
        wait_until(lambda: not window.page.opening)
    elif change == "session":
        window.page.activate_session("history")
        wait_until(lambda: window.controller.session_id == "history")
        window.binding.timer.stop()
    else:
        state = window.presentation.state
        if change == "router":
            state["router"]["router_id"] = "new-router"
        else:
            key = "target_id" if change == "target" else "generation"
            state["context"][key] = "new-value"
            state["router"][key] = "new-value"
        window.renderer.update_session(state)
    assert window.task_panel._cancel_menu is None
    window.router_actions.cancel_router_request(REQUEST_ID, scope)
    assert len(calls) == 1
    window.statusBar().showMessage("Current view notice")
    window.status.setText("Current view status")
    window.commands.error = "Existing error"
    if receipt == "success":
        future.set_result("cancel_requested")
    else:
        future.set_exception(ValueError("Old cancellation failure"))
    window.commands.poll()
    assert window.statusBar().currentMessage() == "Current view notice"
    assert window.status.text() == "Current view status"
    assert window.commands.error == "Existing error"
    assert not notice_text(window)


@pytest.mark.parametrize("request_state", ["running", "timed_out_unknown", "cancel_requested"])
def test_nonqueued_calls_cannot_be_cancelled(window, monkeypatch, request_state):
    calls, _ = deferred_cancel(window, monkeypatch)
    scope = window.router_actions.router_cancel_scope()
    menu, actions = menu_actions(window)
    state = window.presentation.state
    state["router"]["requests"][0]["state"] = request_state
    window.renderer.update_session(state)
    assert not actions[REQUEST_ID].isEnabled()
    window.router_actions.cancel_router_request(REQUEST_ID, scope)
    assert not calls
    menu.close()


@pytest.mark.parametrize("field, value", [
    ("session_id", "other"), ("target_id", "other"), ("task_id", "other"),
    ("task_id", ""), ("request_id", "input"), ("request_id", "tool"),
])
def test_cancel_revalidates_request_identity(window, monkeypatch, field, value):
    calls, _ = deferred_cancel(window, monkeypatch)
    scope = window.router_actions.router_cancel_scope()
    window.presentation.state["router"]["requests"][0][field] = value
    window.router_actions.cancel_router_request(REQUEST_ID, scope)
    assert not calls


@pytest.mark.parametrize("mode", ["opening", "preview", "closing", "fault", "cancelling",
                                 "unavailable", "event_failure", "shutdown"])
def test_read_only_or_unavailable_views_reject_saved_cancel_actions(window, monkeypatch, mode):
    calls, _ = deferred_cancel(window, monkeypatch)
    scope = window.router_actions.router_cancel_scope()
    menu_actions(window)
    if mode in {"opening", "preview", "shutdown"}:
        owner, attribute, value = {
            "opening": (window.page, "opening", True),
            "preview": (window.page, "reviewing", "history"),
            "shutdown": (window.lifecycle, "closing", True),
        }[mode]
        setattr(owner, attribute, value)
    elif mode == "event_failure":
        window.binding.failed = True
    elif mode == "unavailable":
        window.presentation.state["router"]["state"] = "router_unavailable"
    else:
        window.presentation.state[mode] = "unavailable" if mode == "fault" else True
    window.router_actions.refresh_task_panel()
    assert window.task_panel._cancel_menu is None
    assert window.router_actions.router_cancel_scope() is None
    window.router_actions.cancel_router_request(REQUEST_ID, scope)
    assert not calls
    # Fixture cleanup should not request application quit for this synthetic state.
    window.lifecycle.closing = False


def test_multiple_calls_offer_individual_actions_and_remove_stale_requests(window, monkeypatch):
    calls, future = deferred_cancel(window, monkeypatch)
    state = window.presentation.state
    second = dict(state["router"]["requests"][0], request_id="b" * 32, queue_position=2)
    state["router"]["requests"].append(second)
    window.renderer.update_session(state)
    menu, actions = menu_actions(window)
    assert len(actions) == 2
    assert all(action.isEnabled() for action in actions.values())
    assert actions[REQUEST_ID].text() != actions[second["request_id"]].text()
    state["router"]["requests"].pop(0)
    window.renderer.update_session(state)
    assert not actions[REQUEST_ID].isEnabled()
    actions[second["request_id"]].trigger()
    assert calls[0][2] == (second["request_id"],)
    future.set_result("cancel_requested")
    window.commands.poll()
    menu.close()


def test_failed_cancel_after_returning_from_preview_allows_retry_without_old_notice(
    window, monkeypatch,
):
    calls, future = deferred_cancel(window, monkeypatch)
    state = window.presentation.state
    window.router_actions.cancel_router_request(REQUEST_ID, window.router_actions.router_cancel_scope())
    window.page.preview_session("history")
    wait_until(lambda: not window.page.opening)
    window.page.return_from_preview()
    wait_until(lambda: not window.page.opening)
    window.binding.timer.stop()
    window.renderer.update_session(state)
    _, actions = menu_actions(window)
    assert not actions[REQUEST_ID].isEnabled()
    future.set_exception(OSError("old failure"))
    window.commands.poll()
    assert actions[REQUEST_ID].isEnabled()
    assert not window.statusBar().currentMessage() and not window.commands.error
    assert len(calls) == 1


@pytest.mark.parametrize("synchronous", [True, False])
def test_failed_cancel_releases_duplicate_guard_and_preserves_live_status(
    window, monkeypatch, synchronous,
):
    calls, future = deferred_cancel(window, monkeypatch)
    if synchronous:
        def full_queue(*args, **kwargs):
            raise ValueError("queue full")
        monkeypatch.setattr(window.sessions.worker, "command", full_queue)
    _, actions = menu_actions(window)
    actions[REQUEST_ID].trigger()
    if not synchronous:
        future.set_exception(OSError("connection interrupted"))
        window.commands.poll()
    assert "取消未提交" in notice_text(window)
    assert actions[REQUEST_ID].isEnabled()
    assert not window.router_actions._router_cancellations and not window.commands.error
    window.renderer.refresh_status()
    assert "第 1 位" in window.status.text()


def test_target_is_revalidated_in_worker_after_command_queue_wait(window):
    entered, release, calls = threading.Event(), threading.Event(), []
    controller = window.controller

    class Broker:
        def cancel_request(self, *args, **kwargs):
            calls.append(args)
            return "cancel_requested"

    def hold_commands():
        entered.set()
        assert release.wait(5)

    controller.bindings.attach_targets(Broker())
    window.sessions.worker._request(hold_commands)
    try:
        assert entered.wait(2)
        window.router_actions.cancel_router_request(REQUEST_ID, window.router_actions.router_cancel_scope())
        with controller._lock:
            controller.current = BoundContext("instance", "generation", "new-target", {})
    finally:
        release.set()
    wait_until(lambda: not window.commands.pending)
    assert not calls
    assert "target has changed" in notice_text(window)
