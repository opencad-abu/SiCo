# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent gui contract reconcile contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from agent_gui_wait import settle
from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtWidgets import QApplication, QTextBrowser

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal
from sico_ui.transcript import Transcript


def test_queue_hold_reason_explains_a_paused_queue(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "hold") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("hold-instance", "hold-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        settle(window)
        try:
            window.show()
            app.processEvents()
            state = window.controller.updates.read_updates()["snapshot"]
            state.update(paused=True, pending=2)
            state["task"].update(status="needs_reconcile", diagnostic="操作结果没有确认")
            window.renderer.update_session(state)
            reason = window.renderer.queue_hold_reason()
            assert "请先结束中断任务" in reason and "操作结果没有确认" in reason
            assert window.status.text() == "任务已中断 · 待核对"
            assert reason in window.status.toolTip()
            assert window.resume.toolTip() == reason
            assert not window.resume.isEnabled() and window.resume.isVisible()
            assert not window.resume_action.isEnabled()

            state["task"].update(status="running", diagnostic="")
            state["busy"] = True
            window.renderer.update_session(state)
            assert "仍在执行" in window.renderer.queue_hold_reason()
            state["busy"] = False

            state.update(paused=False, pending=0)
            window.renderer.update_session(state)
            assert window.renderer.queue_hold_reason() == ""
            assert window.status.text() != reason
        finally:
            window.close()


def test_abandoning_the_interrupted_task_resumes_the_queue(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "handover") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("handover-instance", "handover-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            calls = []
            window.renderer.resume_queue = lambda *args, **kwargs: calls.append("resume")
            window.presentation.state = {"pending": 1, "fault": False}
            window.renderer._resume_after_reconcile(None)
            assert calls == ["resume"]
            window.presentation.state = {"pending": 0, "fault": False}
            window.renderer._resume_after_reconcile(None)
            assert calls == ["resume"]
            window.presentation.state = {"pending": 2, "fault": True}
            window.renderer._resume_after_reconcile(None)
            assert calls == ["resume"]
        finally:
            window.close()


def test_reconcile_notice_names_the_unconfirmed_operation():
    transcript = Transcript(username="system-user")
    transcript.receive({
        "kind": "task.started",
        "payload": {"origin": "chat", "text": "继续"},
    })
    transcript.receive({
        "kind": "tool.started",
        "payload": {"id": "call-1", "name": "prepare_circuit_library",
                    "input": {"request_id": "ldo-lib-01", "technology_library": "smic28hkmg"}},
    })
    transcript.receive({
        "kind": "tool.finished",
        "payload": {"id": "call-1", "name": "prepare_circuit_library",
                    "result": {"status": "needs_reconcile",
                               "summary": "Context request failed (code=circuit_requires_reconcile)"}},
    })
    transcript.receive({
        "kind": "task.needs_reconcile",
        "payload": {"message": "Context request failed (code=circuit_requires_reconcile, "
                               "method=circuit_call, target_id=bound)"},
        "timestamp": "2026-09-14T09:25:07+00:00",
    })
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view)
    shown = view.toPlainText()
    assert "任务中断，待核对" in shown
    assert "prepare_circuit_library（请求 ldo-lib-01）的结果没有确认" in shown
    assert "circuit_requires_reconcile" in shown
    assert "下一步" in shown and "结束中断任务" in shown


def test_task_completion_retires_the_reconcile_card():
    """任务完成后旧核对卡片必须收拢：不留点不动又消不掉的死按钮。"""
    transcript = Transcript(username="system-user")
    transcript.receive({
        "kind": "task.started",
        "payload": {"origin": "chat", "text": "继续"},
    })
    transcript.receive({
        "kind": "tool.started",
        "payload": {"id": "call-1", "name": "execute_circuit_operation", "input": {}},
    })
    transcript.receive({
        "kind": "tool.finished",
        "payload": {"id": "call-1", "name": "execute_circuit_operation",
                    "result": {"status": "needs_reconcile", "summary": "结果未确认"}},
    })
    transcript.receive({
        "kind": "task.needs_reconcile",
        "payload": {"message": "工具结果没有确认"},
    })
    row = next(row for row in transcript.messages if row["role"] == "reconcile")
    assert row["result"]["active"] is True
    transcript.receive({"kind": "task.completed", "payload": {}})
    assert row["result"]["active"] is False


@pytest.mark.parametrize("live_receipt", [True, False])
def test_reconcile_notice_preserves_failed_creation_receipt(live_receipt):
    transcript = Transcript(username="system-user")
    receipt = dict(ok=False, stage="instances", target=["copilotTest", "LDO", "schematic"],
                   saved=False, error='CDF callback changed requested value: w requested="2u" effective="1u"')
    data = dict(request_id="ldo_exec_1", record_state="response_recorded",
                last_response=receipt, state="target_changed")
    if live_receipt:
        data["receipt"] = receipt
    transcript.receive({"kind": "tool.started", "payload": {
        "id": "tool-call-id", "name": "execute_circuit_operation", "input": {
            "operation": "create_circuit_from_plan", "arguments": {"request_id": "ldo_exec_1"}}}})
    assert transcript.tools["items"][0]["request_id"] == "ldo_exec_1"
    transcript.receive({"kind": "tool.finished", "payload": {
        "id": "tool-call-id", "name": "execute_circuit_operation",
        "result": {"status": "needs_reconcile", "summary": "", "data": data}}})
    transcript.receive({"kind": "task.needs_reconcile", "payload": {
        "message": "execute_circuit_operation: needs_reconcile"}})
    shown = "\n".join(m.get("text", "") for m in transcript.messages)
    assert "请求 ldo_exec_1" in shown and "已返回失败回执" in shown
    assert "copilotTest/LDO/schematic" in shown and "instances" in shown and "未保存" in shown
    assert receipt["error"] in shown and "的结果没有确认" not in shown


def test_interruption_without_dispatched_operations_does_not_ask_for_reconcile():
    transcript = Transcript(username="system-user")
    transcript.receive({
        "kind": "task.needs_reconcile",
        "payload": {"message": "工具执行超过配置时限；请核对原操作结果，不会自动重跑。"},
        "timestamp": "2026-09-14T09:02:01+00:00",
    })
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view)
    shown = view.toPlainText()
    assert "任务已停止" in shown
    assert "工具执行超过配置时限" in shown
    assert "本轮没有已派发操作，已自动结束" in shown
    assert "待核对" not in shown


def test_historical_missing_operation_notice_does_not_imply_lost_reply():
    transcript = Transcript(username="system-user")
    transcript.receive({"kind": "tool.started", "payload": {
        "id": "lookup", "name": "get_circuit_operation",
        "input": {"request_id": "ldo_copilotTest_lib_1"}}})
    transcript.receive({"kind": "tool.finished", "payload": {
        "id": "lookup", "name": "get_circuit_operation",
        "result": {"status": "needs_reconcile", "data": {"code": "operation_not_found"}}}})
    transcript.receive({"kind": "task.needs_reconcile", "payload": {
        "message": "get_circuit_operation: needs_reconcile (operation_not_found)"}})
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view)
    shown = view.toPlainText()
    assert "未找到执行记录" in shown and "ldo_copilotTest_lib_1" in shown
    assert "未创建审阅事项" in shown and "准备请求不产生执行记录" in shown
    assert "的结果没有确认" not in shown and "先查询该请求的现状" not in shown
    transcript.receive({"kind": "task.started", "payload": {"text": "Continue", "origin": "chat"}})
    assert transcript.reconciliation.rows() == []


def test_missing_operation_is_recoverable_and_audit_can_follow():
    transcript = Transcript(username="system-user")
    transcript.receive({"kind": "tool.started", "payload": {
        "id": "lookup", "name": "get_circuit_operation", "input": {"request_id": "preparation"}}})
    transcript.receive({"kind": "tool.finished", "payload": {
        "id": "lookup", "name": "get_circuit_operation",
        "result": {"status": "operation_not_found", "summary": "未找到执行记录", "data": {}}}})
    assert transcript.reconciliation.rows() == []
    for kind, payload in (
        ("workbench.audit.prepared", {"id": "a_domain", "title": "电源域选择", "questions": []}),
        ("workbench.audit.opened", {"id": "a_domain"}),
    ):
        transcript.receive({"kind": kind, "payload": payload, "session_id": "test_session"})
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view)
    shown = view.toPlainText()
    assert "未找到执行记录" in shown and "电源域选择" in shown
    assert "任务中断" not in shown
    assert transcript.audits["a_domain"]["status"] == "pending"


def test_interruption_without_calls_keeps_the_original_diagnostic():
    transcript = Transcript(username="system-user")
    transcript.receive({
        "kind": "task.needs_reconcile",
        "payload": {"message": "Request session is not registered to this desktop"},
        "timestamp": "2026-09-16T05:32:35+00:00",
    })
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view)
    shown = view.toPlainText()
    assert "任务已停止" in shown
    assert "本轮没有已派发操作，已自动结束" in shown
    assert "待核对" not in shown
    assert "Request session is not registered" in shown


def test_registration_loss_keeps_the_unconfirmed_request_details():
    transcript = Transcript(username="system-user")
    transcript.receive({"kind": "tool.started", "payload": {
        "id": "call", "name": "execute_circuit_operation", "input": {"request_id": "ldo-1"}}})
    transcript.receive({"kind": "tool.finished", "payload": {
        "id": "call", "name": "execute_circuit_operation", "result": {
            "status": "needs_reconcile", "data": {"receipt": {
                "ok": False, "saved": False, "target": ["work", "LDO", "schematic"]}}}}})
    text = transcript._reconcile_notice("Request session is not registered to this desktop")
    assert "请求 ldo-1" in text and "work/LDO/schematic" in text and "未保存" in text
    assert "不要直接重跑" in text and "会话注册已失效" in text
