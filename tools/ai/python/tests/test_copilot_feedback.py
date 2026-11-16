"""Regressions from live Copilot sessions: visible questions and retained project scope."""

import json
import os
import queue
import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest
from agent_gui_wait import receipts, settle, wait_until
from test_agent_workbench import backend_for, begin

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QUrl, Qt
from PyQt5.QtWidgets import QApplication, QRadioButton

from sico.codex.backend import CodexBackend
from sico.core.contracts import BoundContext, ModelTurn, NeedsReconcile, TextDelta, ToolResult
from sico.core.tools import ReadTool, ToolRegistry, no_arguments
from sico.providers.mock import ScriptedProvider
from sico.service.python_backend import PythonBackend
from sico.service.controller import SessionController
from sico.service.workbench import attach_workbench
from sico.storage.journal import SessionJournal
from sico.storage.workbench import object_link
from agent_window_fixture import create_window as AssistantWindow


def native_feedback_backend(journal, *, start=True):
    context = BoundContext("instance", "generation", "source", {"valid": True})
    backend = CodexBackend(ScriptedProvider([]), ToolRegistry(), journal, context)
    backend.workbench = attach_workbench(backend)
    if start:
        backend._begin("复核归档结果", context, None, "chat")
        from codex_thread_fixture import bind_thread
        bind_thread(backend)
        backend.active, backend.cancelled = True, lambda: False
        backend.runtime = SimpleNamespace(rpc=SimpleNamespace(
            send=lambda message, **kwargs: None, poll_notifications=lambda: None,
            poll_requests=lambda handler: None),
                                          close=lambda: None)
        backend.validate_audit_target = lambda: None
    return backend


@pytest.mark.parametrize("width,height", [(800, 600), (1280, 820)])
def test_pending_questions_are_visible_answerable_and_replayable(tmp_path, width, height):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "questions") as journal:
        backend = native_feedback_backend(journal)
        controller = SessionController(backend)
        window = AssistantWindow(controller)
        window.resize(width, height)
        window.show()
        questions = [{
            "id": "mode", "header": "运行方式", "question": "本次 LDO 设计采用前台还是后台？",
            "options": [{"label": "前台", "description": "在当前 Virtuoso 中执行设计与仿真。"},
                        {"label": "后台", "description": "后台运行。"}],
        }, {
            "id": "spec", "header": "设计规格", "question": "输入输出电压是多少？", "options": [],
        }]
        backend.audit.open({"id": "rpc_question", "params": {
            "threadId": "thread", "turnId": "turn", "itemId": "item", "isBlocking": False,
            "questions": questions,
        }})
        key = next(iter(backend.audit.pending))
        backend.active = True
        backend.cancelled = lambda: False
        captured = []
        backend.runtime.rpc.send = lambda message, **kwargs: captured.append(message)
        try:
            settle(window)
            app.processEvents()
            shown = window.display.toPlainText()
            assert "本次 LDO 设计采用前台还是后台" in shown
            assert "输入输出电压是多少" in shown and "答复这些问题" in shown
            assert "rpc_question" not in shown
            panel = window.centers.audit
            # prepared -> pending must rebuild even when the selected row does not change.
            assert not panel.editor_scroll.isHidden()
            link = QUrl(object_link("audit", journal.session_id, key))
            window.display.anchorClicked.emit(link)
            assert window.centers.tabs.currentWidget() is panel
            panel.answer.click()
            assert panel.validation.text() == "尚未答复：运行方式、设计规格"
            assert not captured
            next(b for b in panel.choices.findChildren(QRadioButton) if b.property("choice") == "前台").click()
            panel.text_edits["spec"].setPlainText("Vin 1.8 V，Vout 0.9 V")
            panel.render_audit(key)
            assert panel.text_edits["spec"].toPlainText() == "Vin 1.8 V，Vout 0.9 V"
            assert any(b.isChecked() for b in panel.choices.findChildren(QRadioButton))
            app.processEvents()
            assert window.width() <= width and window.height() <= height
            assert panel.editor_scroll.horizontalScrollBar().maximum() == 0
            window.grab().save(str(tmp_path / "pending-questions.png"))
            # The narrow 800x600 editor lays out deferred; keep scrolling until
            # the answer button is actually inside the viewport.
            for _ in range(10):
                panel.editor_scroll.ensureWidgetVisible(panel.answer)
                app.processEvents()
                visible = panel.editor_scroll.viewport().rect().intersects(
                    panel.answer.rect().translated(
                        panel.answer.mapTo(
                            panel.editor_scroll.viewport(), panel.answer.rect().topLeft()
                        )
                    )
                )
                if visible:
                    break
            assert panel.answer.geometry().left() <= 8 + 2
            assert visible
            window.grab().save(str(tmp_path / "answer-button-visible.png"))
            panel.answer.click()
            settle(window)
            row = backend.audit.index.audits[key]
            assert row["status"] == "answer_received"
            assert row["reply"]["answers"]["mode"]["choice"] == "前台"
            assert not controller.inbox.pending
            backend.audit.deliver()
            assert captured[0]["result"]["answers"]["spec"]["answers"] == ["Vin 1.8 V，Vout 0.9 V"]
            # dispatching/resumed arrive as journal events after delivery.
            wait_until(lambda: "已答复，任务继续" in window.display.toPlainText())
            assert "已答复，任务继续" in window.display.toPlainText()
            assert "Vin 1.8 V，Vout 0.9 V" in window.display.toPlainText()
            assert panel.editor_scroll.isHidden()
            backend._finish("completed")
            settle(window)
        finally:
            window.close()
            assert window.sessions.wait(5)
            window.close()
            window.deleteLater()
            app.processEvents()
        reopened = AssistantWindow(native_feedback_backend(journal, start=False))
        try:
            settle(reopened)
            assert "已答复，任务继续" in reopened.display.toPlainText()
            assert "答复这些问题" not in reopened.display.toPlainText()
        finally:
            reopened.close()


def test_pending_audit_form_stays_at_top_with_bottom_whitespace(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "audit-top") as journal:
        backend = native_feedback_backend(journal)
        controller = SessionController(backend)
        window = AssistantWindow(controller)
        window.resize(800, 820)
        window.show()
        backend.audit.open({"id": "rpc", "params": {
            "threadId": "thread", "turnId": "turn", "itemId": "item", "isBlocking": False,
            "questions": [{"id": "a", "header": "问题一", "question": "第一个问题？", "options": []},
                          {"id": "b", "header": "问题二", "question": "第二个问题？", "options": []},
                          {"id": "c", "header": "问题三", "question": "第三个问题？", "options": []}],
        }})
        try:
            settle(window)
            panel = window.centers.audit
            window.centers.tabs.setCurrentWidget(panel)
            panel.render_audit(next(iter(panel.audit_rows)))
            app.processEvents()
            assert panel.editor_scroll.alignment() & Qt.AlignTop
            assert panel.editor.geometry().top() == 0
            assert panel.choices.height() == panel.choices.sizeHint().height()
            assert panel.answer.geometry().bottom() < panel.editor.height()
        finally:
            window.close()


class FakeRpc:
    def __init__(self):
        self.requests = []
        self.events = queue.Queue()

    def request(self, method, params):
        self.requests.append((method, params))
        if method == "turn/start":
            self.events.put({"method": "item/completed", "params": {
                "threadId": "thread", "turnId": "turn", "item": {
                    "type": "agentMessage", "id": "message", "text": "继续工程任务。",
                },
            }})
            self.events.put({"method": "turn/completed", "params": {
                "threadId": "thread", "turn": {"id": "turn", "status": "completed"},
            }})
            return {"turn": {"id": "turn"}}
        return {}

    def next(self):
        return self.events.get_nowait()


@pytest.mark.parametrize("project_valid", [True, False])
def test_closed_entry_window_continues_only_in_original_project(tmp_path, project_valid):
    context = BoundContext("instance", "generation", "source", {
        "valid": True, "cwd": str(tmp_path), "window_ref": "wctx_test_1",
        "capabilities": ["get_project_context", "get_entry_context"],
    })
    registry, calls = ToolRegistry(), []
    def project(args, bound):
        calls.append(bound.record())
        if not project_valid:
            raise NeedsReconcile("工程目录已改变，请重新打开会话。")
        return ToolResult(data={"valid": True, "source_valid": False, "cwd": str(tmp_path)})
    registry.register(ReadTool("get_project_context", "project", {"type": "object"}, no_arguments, project))
    registry.register(ReadTool("get_entry_context", "entry", {"type": "object"}, no_arguments,
                               lambda args, bound: pytest.fail("Closed entry must not gate project tasks")))
    with SessionJournal(tmp_path, "project") as journal:
        backend = CodexBackend(None, registry, journal, context)
        rpc = FakeRpc()
        backend.runtime = SimpleNamespace(rpc=rpc, close=lambda: None)
        from codex_thread_fixture import bind_thread
        bind_thread(backend)
        events = list(backend.run("继续创建", input_id="next"))
        assert calls == [context.record()]
        if project_valid:
            assert events[-1]["kind"] == "task.completed"
            assert any(e["kind"] == "context.detached" for e in events)
            injected = next(p for m, p in rpc.requests if m == "thread/inject_items")
            assert 'source_valid' in json.dumps(injected)
            assert backend.state.context.record() == context.record()
        else:
            assert events[-1]["kind"] == "task.needs_reconcile"
            assert "工程目录已改变" in events[-1]["payload"]["message"]
            assert not rpc.requests
            assert not any(e["kind"] == "model.status" and e["payload"]["phase"] == "waiting_model" for e in events)
        backend.close()


def test_new_request_after_stop_starts_without_empty_queue_resume(tmp_path):
    with SessionJournal(tmp_path, "queue") as journal:
        backend = backend_for(journal, ScriptedProvider([[TextDelta("完成"), ModelTurn("完成")]]))
        begin(backend)
        backend._finish("cancelled")
        controller = SessionController(backend)
        controller.cancel()
        controller.submit("新任务")
        assert controller.wait(5)
        assert not controller.paused
        assert backend.state.task["status"] == "completed"
        controller.close()


def test_waiting_question_button_keeps_chat_out_of_queue(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "waiting") as journal:
        backend = native_feedback_backend(journal)
        controller = SessionController(backend)
        window = AssistantWindow(controller)
        backend.audit.open({"id": "rpc", "params": {
            "threadId": "thread", "turnId": "turn", "itemId": "item", "isBlocking": False,
            "questions": [{"id": "spec", "header": "规格", "question": "目标电压是多少？", "options": []}],
        }})
        try:
            window.binding.poll()
            state = deepcopy(controller.updates.read_updates()["snapshot"])
            state.update(busy=True, task=deepcopy(backend.state.task))
            window.renderer.update_session(state)
            assert window.send.text() == "答复问题"
            window.input.setPlainText("0.9 V")
            window.send.click()
            receipts(window)
            assert not controller.inbox.pending
            assert window.input.toPlainText() == "0.9 V"
            assert window.centers.tabs.currentWidget() is window.centers.audit
            wait_until(lambda: not window.centers.audit.editor_scroll.isHidden())
        finally:
            window.close()


def test_queue_controls_only_offer_existing_pending_work(tmp_path):
    app = QApplication.instance() or QApplication([])
    entered, release = threading.Event(), threading.Event()
    class Provider:
        def stream(self, *args):
            entered.set()
            assert release.wait(5)
            yield ModelTurn("")
    with SessionJournal(tmp_path, "controls") as journal:
        backend = PythonBackend(Provider(), ToolRegistry(), journal, BoundContext("i", "g", "b", {}))
        window = AssistantWindow(backend)
        try:
            assert window.resume.isHidden() and not window.resume_action.isVisible()
            window.controller.submit("first")
            assert entered.wait(5)
            window.controller.submit("second")
            window.binding.poll()
            # The stop control enables only after the busy snapshot arrives.
            wait_until(lambda: window.stop.isEnabled())
            window.stop.click()
            settle(window, tasks=False)
            release.set()
            assert window.controller.wait(5)
            wait_until(lambda: not window.resume.isHidden() and window.resume.isEnabled())
            assert "停止当前任务" in window.stop.toolTip()
            assert window.resume.text() == "执行待办任务（1）"
            window.resume.click()
            wait_until(lambda: window.presentation.state is not None
                       and window.presentation.state["pending"] == 0)
            assert window.resume.isHidden() and not window.resume_action.isVisible()
            assert window.presentation.state["pending"] == 0
        finally:
            release.set()
            window.controller.wait(5)
            window.close()
