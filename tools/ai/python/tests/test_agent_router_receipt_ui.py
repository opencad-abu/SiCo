"""Receipt dialogs consume worker text and discard stale page results."""

import os
import threading
from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace

import pytest
from agent_gui_wait import settle, wait_until
from info_app_fixtures import dismiss_notice, notice_text

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sico.service.router_commands as controller_module
from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QPlainTextEdit
from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.service.router_receipt import RouterReceiptView
from sico.storage.journal import SessionJournal

REQUEST_ID = "a" * 32


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    context = BoundContext("instance", "generation", "target", {})
    with SessionJournal(tmp_path, "history") as journal:
        PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, context)._event(
            "session.created", {"context": context.record()},
        )
    with SessionJournal(tmp_path, "initial") as journal:
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, context)
        loop._begin("Read design", None, None)
        loop._event("task.started", {"text": "Read design", "context": context.record()})
        loop._event("router.status", {
            "request_id": REQUEST_ID, "session_id": "initial", "task_id": loop.state.task["id"],
            "target_id": "target", "router_id": "router", "state": "completed",
        })
        loop._finish("completed")
        widget = AssistantWindow(loop, persistent=True)
        try:
            settle(widget)
            widget.binding.timer.stop()
            widget.resize(960, 640)
            widget.show()
            yield widget
        finally:
            widget.sessions.close()
            assert widget.sessions.wait(5)
            widget.lifecycle.persistent = False
            widget.close()
            widget.deleteLater()
            app.processEvents()


def display_view(window):
    return RouterReceiptView(window.controller.session_id, window.controller.runtime_id,
                             window.presentation.displayed_context, REQUEST_ID, "Receipt title", "Worker text")


def defer_query(window, monkeypatch):
    calls, futures = [], []

    def command(controller, name, **kwargs):
        calls.append((controller, name, kwargs))
        futures.append(Future())
        return futures[-1]

    monkeypatch.setattr(window.sessions.worker, "command", command)
    return calls, futures


def switch_page(window, mode):
    if mode in {"preview", "roundtrip"}:
        window.page.preview_session("history")
        wait_until(lambda: not window.page.opening)
        if mode == "roundtrip":
            window.page.return_from_preview()
            wait_until(lambda: not window.page.opening)
            wait_until(lambda: window.presentation.state is not None)
            window.binding.timer.stop()
    elif mode == "session":
        window.page.activate_session("history")
        wait_until(lambda: not window.page.opening)
        wait_until(lambda: window.presentation.state is not None)
        assert window.controller.session_id == "history"
        window.binding.timer.stop()
    elif mode in {"target", "generation"}:
        state = dict(window.presentation.state)
        state["context"] = dict(state["context"], **{
            "target_id" if mode == "target" else "generation": "changed",
        })
        window.renderer.update_session(state)
    elif mode == "shutdown":
        window.lifecycle.closing = True
        window.clear_data_receipts()
    elif mode == "runtime":
        window.controller.runtime_id = "new-runtime"
        window.router_actions.refresh_router_receipt()
    elif mode == "event_failure":
        window.binding.fail()


@pytest.mark.parametrize("stage", ["read", "format"])
def test_slow_query_and_formatting_leave_qt_responsive(window, monkeypatch, stage):
    controller, owner = window.controller, threading.get_ident()
    entered, release, ticks, threads = threading.Event(), threading.Event(), [], []
    format_view = controller_module.router_receipt_view

    def hold():
        entered.set()
        assert release.wait(5)

    def read(context, request_id, *, session_id):
        threads.append(threading.get_ident())
        assert threads[-1] != owner
        assert (context, request_id, session_id) == (controller.current, REQUEST_ID, "initial")
        if stage == "read":
            hold()
        return dict(instance_id="instance", generation="generation", target_id="target",
                    request_id=request_id, session_id=session_id, router_id="router",
                    state="completed", automatic_resume_allowed=False)

    def formatting(*args):
        threads.append(threading.get_ident())
        assert threads[-1] != owner
        if stage == "format":
            hold()
        return format_view(*args)

    controller.bindings.attach_targets(SimpleNamespace(request_receipt=read))
    monkeypatch.setattr(controller_module, "router_receipt_view", formatting)
    timer = QTimer(window)
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    try:
        window.receipt_action.trigger()
        assert entered.wait(2)
        assert not window.receipt_action.isEnabled()
        assert window.router_actions.query_router_receipt() is None
        wait_until(lambda: len(ticks) >= 3, timeout=2)
        window.input.setFocus()
        QTest.keyClicks(window.input, "draft during query")
        assert window.input.toPlainText() == "draft during query"
        assert window.findChild(QPlainTextEdit, "routerReceipt") is None
    finally:
        release.set()
        timer.stop()
    wait_until(lambda: not window.commands.pending)
    editor = window.findChild(QPlainTextEdit, "routerReceipt")
    assert editor is not None and editor.isReadOnly()
    assert '"automatic_resume_allowed": false' in editor.toPlainText()
    assert len(threads) == 2 and window.receipt_action.isEnabled()
    assert not window.controller.paused and not window.controller._cancel.is_set()


@pytest.mark.parametrize("outcome", ["success", "failure"])
@pytest.mark.parametrize("mode", ["preview", "session", "roundtrip", "target", "generation",
                                 "runtime", "shutdown", "event_failure"])
def test_late_query_result_cannot_cross_page_boundary(window, monkeypatch, outcome, mode):
    _, futures = defer_query(window, monkeypatch)
    view = display_view(window)
    window.router_actions.query_router_receipt()
    switch_page(window, mode)
    if mode == "event_failure":
        assert "会话事件无法同步" in notice_text(window)
        dismiss_notice(window)
    window.statusBar().showMessage("Current page notice")
    window.status.setText("Current page status")
    window.commands.error = "Existing error"
    if outcome == "success":
        futures[0].set_result(view)
    else:
        futures[0].set_exception(ValueError("Old query failure"))
    window.commands.poll()
    assert window.router_actions._router_receipt_dialog is None
    assert window.statusBar().currentMessage() == "Current page notice"
    assert window.status.text() == "Current page status"
    assert window.commands.error == "Existing error"
    assert not notice_text(window)


def test_requery_after_return_ignores_earlier_receipt_and_closes_dialog_on_preview(
    window, monkeypatch,
):
    calls, futures = defer_query(window, monkeypatch)
    view = display_view(window)
    window.router_actions.query_router_receipt()
    assert calls[0] == (window.page.session, "router_receipt",
                        {"context": window.presentation.displayed_context, "formatted": True})
    switch_page(window, "roundtrip")
    assert window.receipt_action.isEnabled()
    window.router_actions.query_router_receipt()
    futures[0].set_result(view)
    window.commands.poll()
    assert window.router_actions._router_receipt_dialog is None and not window.receipt_action.isEnabled()
    futures[1].set_result(replace(view, text="New worker text"))
    window.commands.poll()
    editor = window.findChild(QPlainTextEdit, "routerReceipt")
    assert editor.toPlainText() == "New worker text" and editor.isReadOnly()
    # 回执窗口是 SiCo 自绘对话框：标题写在自绘标题栏上。
    assert editor.window().windowTitle() == "Receipt title"
    assert editor.window().title_bar.title.text() == "Receipt title"
    switch_page(window, "preview")
    assert window.router_actions._router_receipt_dialog is None and not window.receipt_action.isEnabled()
    window.router_actions.query_router_receipt()
    assert len(calls) == 2


@pytest.mark.parametrize("field", ["session_id", "runtime_id", "context"])
def test_mismatched_presentation_identity_is_rejected(window, monkeypatch, field):
    _, futures = defer_query(window, monkeypatch)
    view = display_view(window)
    value = replace(view.context, target_id="other") if field == "context" else "other"
    window.router_actions.query_router_receipt()
    futures[0].set_result(replace(view, **{field: value}))
    window.commands.poll()
    assert window.router_actions._router_receipt_dialog is None
    assert "来源不匹配" in notice_text(window)
    assert window.receipt_action.isEnabled()


@pytest.mark.parametrize("immediate", [False, True])
def test_query_failure_restores_action_without_pinning_status(window, monkeypatch, immediate):
    _, futures = defer_query(window, monkeypatch)
    if immediate:
        def full_queue(*args, **kwargs):
            raise ValueError("queue full")
        monkeypatch.setattr(window.sessions.worker, "command", full_queue)
    window.router_actions.query_router_receipt()
    if not immediate:
        futures[0].set_exception(OSError("read failed"))
        window.commands.poll()
    assert "回执查询失败" in notice_text(window)
    assert window.receipt_action.isEnabled() and window.router_actions._router_receipt_query is None
    assert not window.commands.error


@pytest.mark.parametrize("mode", ["preview", "shutdown", "event_failure"])
def test_unavailable_page_cannot_start_query(window, monkeypatch, mode):
    calls, _ = defer_query(window, monkeypatch)
    switch_page(window, mode)
    assert not window.receipt_action.isEnabled()
    window.router_actions.query_router_receipt()
    assert not calls
