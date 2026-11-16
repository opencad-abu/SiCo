"""Real composer buttons and worker receipts preserve scope and user drafts."""

from types import SimpleNamespace

import pytest
from agent_gui_wait import settle, wait_until
from agent_window_fixture import create_window as AssistantWindow
from info_app_fixtures import notice_text
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QTextCursor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from sico.codex.backend import CodexBackend
from sico.codex.rpc import RpcError, RpcRejected
from sico.core.cad_ref import object_ref
from sico.core.contracts import BoundContext, json_copy
from sico.core.tools import ToolRegistry
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal
from test_agent_codex_native import CONTEXT
from test_codex_steering import rpc_for


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def window(tmp_path, app):
    provider = SimpleNamespace(options={}, environment={})
    with SessionJournal(tmp_path, "steer-window") as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        backend._begin("检查当前设计", CONTEXT, "original", "chat")
        from codex_thread_fixture import bind_thread
        bind_thread(backend)
        backend.active, backend.cancelled = True, lambda: False
        backend.native_history.bind("turn")
        backend.steering.open("turn")
        controller = SessionController(backend)
        controller._running = True
        widget = AssistantWindow(controller)
        widget.show()
        try:
            settle(widget, tasks=False)
            wait_until(lambda: widget.send.isEnabled() and widget.send.text() == "补充当前任务")
            yield widget
        finally:
            backend.runtime = None
            backend.steering.end()
            controller._running = False
            widget.close()
            assert widget.sessions.wait(5)
            widget.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()


def publish(window):
    controller = window.controller
    controller._task = json_copy(controller.loop.state.task)
    controller.version += 1
    window.binding.poll()


@pytest.mark.parametrize("status", ["accepted", "rejected", "unconfirmed"])
@pytest.mark.parametrize("width", [980, 1500])
def test_supplement_button_receipt_and_history(window, tmp_path, status, width):
    backend = window.controller.loop
    error = {"rejected": RpcRejected("ended"), "unconfirmed": RpcError("timeout")}.get(status)
    calls = rpc_for(backend, response={"turnId": "turn"}, error=error)
    window.resize(width, 800)
    window.input.setPlainText("使用新上限 17，并记录对照结果。")
    button = window.send
    queue_action = window.steering_input.queue_action
    menu_button = window.steering_input.menu_button
    assert button.isEnabled()
    button.clicked.emit(True)
    wait_until(lambda: backend.steering.pending is not None)
    assert not button.isEnabled() and not queue_action.isEnabled()
    assert window.input.toPlainText()
    backend.steering.dispatch()
    publish(window)
    settle(window, tasks=False)
    assert len(calls) == 1 and calls[0][0] == "turn/steer"
    assert not window.controller.inbox.pending
    assert bool(window.input.toPlainText()) == (status != "accepted")
    assert len(window.presentation.transcript.supplements) == 1
    assert "使用新上限 17" in window.display.toPlainText()
    assert not window.binding.failed
    # The send button and its adjoining arrow fit the composer at both sizes.
    assert window.width() <= width
    assert button.geometry().right() < menu_button.geometry().left()
    assert menu_button.geometry().right() < window.stop.geometry().left()
    wait_until(lambda: button.isEnabled() == (status != "unconfirmed"))
    wait_until(queue_action.isEnabled)
    assert window.grab().save(str(tmp_path / f"steering-{status}-{width}.png"))


def test_receipt_does_not_clear_edited_draft(window):
    backend = window.controller.loop
    rpc_for(backend, response={"turnId": "turn"})
    window.input.setPlainText("原始补充")
    window.steering_input.submit(True)
    wait_until(lambda: backend.steering.pending is not None)
    window.input.setPlainText("继续编辑的新草稿")
    backend.steering.dispatch()
    publish(window)
    settle(window, tasks=False)
    assert window.input.toPlainText() == "继续编辑的新草稿"


def test_supplement_returns_a_frozen_reader_to_the_live_tail(window):
    backend = window.controller.loop
    rpc_for(backend, response={"turnId": "turn"})
    window.presentation.transcript.append(
        "assistant", "\n\n".join(f"历史消息 {i}：一直往下翻阅" for i in range(180))
    )
    window.renderer.flush()
    QApplication.instance().processEvents()
    bar = window.display.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum() - 8)
    assert window.chat_scroll.history is not None
    window.input.setPlainText("补充：记录对照结果。")
    window.steering_input.submit(True)
    wait_until(lambda: backend.steering.pending is not None)
    assert window.chat_scroll.history is None


@pytest.mark.parametrize("operation", ["switch", "preview"])
def test_late_receipt_cannot_change_another_view(window, tmp_path, operation):
    original = window.controller
    backend = original.loop
    rpc_for(backend, response={"turnId": "turn"})
    with SessionJournal(tmp_path, "other-session") as journal:
        other = SessionController(CodexBackend(backend.provider, ToolRegistry(), journal, CONTEXT))
        window.sessions.controllers[other.session_id] = other
        window.input.setPlainText("仅属于原始会话的补充")
        window.steering_input.submit(True)
        wait_until(lambda: backend.steering.pending is not None)
        if operation == "switch":
            window.page.activate_session(other.session_id)
        else:
            window.page.preview_session(other.session_id)
        # Receipt is still pending, so settle the opening independently.
        wait_until(lambda: not window.page.opening)
        window.input.setPlainText("保留此视图的草稿")
        backend.steering.dispatch()
        original._task = json_copy(backend.state.task)
        original.version += 1
        settle(window, tasks=False)
        assert window.input.toPlainText() == "保留此视图的草稿"
        assert not window.submissions
        assert not window.submissions
        assert window.steering_input.scope() is None
        if operation == "preview":
            wait_until(lambda: not window.send.isEnabled()
                       and not window.steering_input.queue_action.isEnabled())
        else:
            wait_until(lambda: window.send.isEnabled() and window.send.text() == "发送")
        window.page.activate_session(original.session_id)
        settle(window, tasks=False)
        assert not window.binding.failed
        assert len(window.presentation.transcript.supplements) == 1
        wait_until(lambda: window.send.isEnabled() and window.send.text() == "补充当前任务")


@pytest.mark.parametrize("boundary", ["source", "audit", "attachment", "cancel"])
def test_composer_rejects_unbound_or_dependent_input(window, boundary):
    backend = window.controller.loop
    calls = rpc_for(backend)
    window.input.setPlainText("保留草稿")
    if boundary == "source":
        window.presentation.submission_context = BoundContext("instance", "generation", "other", {})
    elif boundary == "audit":
        backend.state.task["waiting_audits"] = ["pending-question"]
        publish(window)
    elif boundary == "attachment":
        window.input._text_attachment = "完整附件内容"
    else:
        window.controller.cancel()
        publish(window)
    window.steering_input.refresh()
    window.steering_input.submit(True)
    settle(window, tasks=False)
    assert window.input.toPlainText() == "保留草稿"
    assert not calls and not backend.steering.pending and not window.controller.inbox.pending


def test_explicit_queue_keeps_independent_tasks_in_order(window):
    backend = window.controller.loop
    calls = rpc_for(backend)
    window.input.setPlainText("独立排队任务一")
    window.steering_input.queue_action.trigger()
    settle(window, tasks=False)
    window.input.setPlainText("独立排队任务二")
    window.steering_input.queue_action.trigger()
    settle(window, tasks=False)
    assert [row["message"]["text"] for row in window.controller.inbox.pending] == [
        "独立排队任务一", "独立排队任务二",
    ]
    assert not backend.steering.pending and not calls
    assert not window.input.toPlainText()


@pytest.mark.parametrize("action", ["enter", "keypad", "click"])
def test_primary_composer_steers_the_active_turn(window, action):
    backend = window.controller.loop
    calls = rpc_for(backend, response={"turnId": "turn"})
    window.input.setPlainText("把当前任务的限制改为 17")
    if action == "click":
        QTest.mouseClick(window.send, Qt.LeftButton)
    else:
        QTest.keyClick(window.input, Qt.Key_Return if action == "enter" else Qt.Key_Enter)
    wait_until(lambda: backend.steering.pending is not None)
    assert not window.controller.inbox.pending
    backend.steering.dispatch()
    publish(window)
    settle(window, tasks=False)
    assert [call[0] for call in calls] == ["turn/steer"]
    assert window.input.toPlainText() == ""
    assert len(window.presentation.transcript.supplements) == 1


@pytest.mark.parametrize("boundary", ["ended", "cancelled", "unconfirmed"])
def test_busy_without_usable_scope_never_falls_back_to_queue(window, boundary):
    backend = window.controller.loop
    calls = rpc_for(backend)
    if boundary == "ended":
        backend.steering.end()
    elif boundary == "cancelled":
        window.controller.cancel()
    else:
        backend.state.task["steer_blocked"] = True
    publish(window)
    settle(window, tasks=False)
    window.input.setPlainText("仍属于当前任务的补充")
    wait_until(lambda: not window.send.isEnabled())
    assert window.send.text() == "补充当前任务"
    QTest.keyClick(window.input, Qt.Key_Return)
    settle(window, tasks=False)
    assert window.input.toPlainText() == "仍属于当前任务的补充"
    assert not calls and not backend.steering.pending and not window.controller.inbox.pending


def test_turn_ending_after_display_preserves_draft(window):
    backend = window.controller.loop
    calls = rpc_for(backend)
    # The UI still displays the previous scope when the native turn ends.
    backend.steering.end()
    window.input.setPlainText("迟到的补充")
    window.actions.submit()
    settle(window, tasks=False)
    assert window.input.toPlainText() == "迟到的补充"
    assert not calls and not backend.steering.pending and not window.controller.inbox.pending


def test_full_task_queue_still_allows_current_turn_input(window):
    backend = window.controller.loop
    calls = rpc_for(backend, response={"turnId": "turn"})
    for index in range(16):
        window.controller.submit(f"独立任务 {index}")
    publish(window)
    settle(window, tasks=False)
    wait_until(lambda: not window.steering_input.queue_action.isEnabled())
    assert window.send.isEnabled()
    window.input.setPlainText("本任务补充无需等待队列")
    window.send.click()
    wait_until(lambda: backend.steering.pending is not None)
    backend.steering.dispatch()
    publish(window)
    settle(window, tasks=False)
    assert len(calls) == 1 and len(window.controller.inbox.pending) == 16
    assert not window.input.toPlainText()


def test_object_reference_is_forwarded_as_model_data(window):
    backend = window.controller.loop
    calls = rpc_for(backend, response={"turnId": "turn"})
    target = {
        "library": "worklib", "cell": "OTA", "view_name": "schematic",
        "oa_view_type": "schematic", "oa_view_type_source": "platform_api",
    }
    instance = {
        "kind": "instance", "name": "NM1",
        "master": {"library": "smic18", "cell": "nmos4", "view_name": "symbol"},
        "hierarchy": [],
    }
    window.input.setPlainText("按新尺寸检查 ")
    window.input.moveCursor(QTextCursor.End)
    window.input.insert_object_ref(object_ref(target, instance), "NM1")
    window.send.click()
    wait_until(lambda: backend.steering.pending is not None)
    backend.steering.dispatch()
    publish(window)
    settle(window, tasks=False)
    assert len(calls) == 1 and calls[0][0] == "turn/steer"
    text = calls[0][1]["input"][0]["text"]
    assert "User-selected CAD objects" in text
    assert '"name": "NM1"' in text
    assert not window.input.object_inputs()


def test_attachment_preserved_for_explicit_queue(window):
    backend = window.controller.loop
    calls = rpc_for(backend)
    window.input.setPlainText("附上完整文本")
    window.input._text_attachment = "完整附件内容"
    QTest.keyClick(window.input, Qt.Key_Return)
    assert window.input.draft()[:2] == ("附上完整文本", "完整附件内容")
    assert "提示 · 补充暂未发送" in notice_text(window)
    assert "发送按钮的下拉菜单" in notice_text(window)
    assert not backend.steering.pending and not window.controller.inbox.pending
    window.steering_input.queue_action.trigger()
    settle(window, tasks=False)
    assert len(window.controller.inbox.pending) == 1 and not calls
    assert window.input.draft()[:2] == ("", None)


def test_pending_question_keeps_reply_action_and_explicit_queue(window, monkeypatch):
    backend = window.controller.loop
    calls = rpc_for(backend)
    backend.state.task["waiting_audits"] = ["pending-question"]
    publish(window)
    settle(window, tasks=False)
    shown = []
    monkeypatch.setattr(window.actions, "show_pending_audit", lambda: shown.append(True))
    window.input.setPlainText("稍后独立处理的需求")
    assert window.send.text() == "答复问题"
    QTest.keyClick(window.input, Qt.Key_Return)
    assert shown == [True] and not window.controller.inbox.pending
    window.steering_input.queue_action.trigger()
    settle(window, tasks=False)
    assert len(window.controller.inbox.pending) == 1
    assert not calls and not backend.steering.pending


def test_other_target_requires_explicit_later_task(window):
    backend = window.controller.loop
    calls = rpc_for(backend)
    target = BoundContext("instance", "generation", "other", {})
    window.controller.bindings.events.default_context = target
    window.presentation.submission_context = target
    window.steering_input.refresh()
    window.input.setPlainText("检查另一个目标")
    assert window.send.text() == "发送" and not window.send.isEnabled()
    QTest.keyClick(window.input, Qt.Key_Return)
    settle(window, tasks=False)
    assert not window.controller.inbox.pending
    assert window.input.toPlainText() == "检查另一个目标"
    assert "当前任务完成后执行" in notice_text(window)
    window.steering_input.queue_action.trigger()
    settle(window, tasks=False)
    pending = window.controller.inbox.pending
    assert len(pending) == 1 and pending[0]["message"]["context"] == target.record()
    assert not calls and not backend.steering.pending
