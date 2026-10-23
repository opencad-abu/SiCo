"""Exact-request observations and explicit decisions outside the paused task queue."""

import json
import uuid

from ..core.contracts import NeedsReconcile, ToolCall
from ..storage.journal import BULK_KINDS
from ..transport.broker import skill_call_context
from .published import freeze
from .reconcile_calls import from_events
from .reconcile_audit import audit_fields
from .router_receipt import validate_router_receipt


class SessionReconciliation:
    def __init__(self, controller):
        self.controller = controller

    def _calls(self, expected_task, *, allow_busy=False):
        owner = self.controller
        if ((owner.busy and not allow_busy) or owner.closing or owner._shutdown.is_set()
                or owner._task.get("id") != expected_task
                or owner._task.get("status") != "needs_reconcile"):
            raise ValueError("原中断任务已变化，请刷新后核对")
        return from_events(owner.journal.events(exclude=BULK_KINDS), expected_task)

    def query_interrupted(self, call_id, *, expected_task):
        owner = self.controller
        with owner._lock:
            calls = self._calls(expected_task)
            row = next((row for row in calls.rows() if row["id"] == call_id), None)
            if row is None:
                raise ValueError("原任务没有这项已派发调用")
            context = owner.loop.state.context
        try:
            evidence = self._query(row, context, expected_task)
            text = "原请求核对 · " + row["name"] + " · 调用 " + call_id + "\n" + json.dumps(
                evidence, ensure_ascii=False, indent=2)
        except (NeedsReconcile, ValueError, OSError, RuntimeError) as exc:
            text = "原请求核对未确认 · 调用 " + call_id + "\n" + str(exc)
        text += "\n查询不会重发原调用；请据证据选择结果，仍不确定时保持暂停。"
        with owner._lock:
            self._calls(expected_task)
            owner.journal.append_session("session.reconcile_queried",
                dict(call_id=call_id, text=text, **audit_fields()), context)
            owner.version += 1
        return dict(status="needs_reconcile", message=text)

    def _query(self, row, context, task_id):
        owner = self.controller
        if row.get("late_result") is not None:
            return dict(source="original_late_receipt", result=row["late_result"])
        # Only the fixed, read-only status tool can be invoked, never the original name/input.
        if row["name"] in {"execute_circuit_operation", "get_circuit_operation"} and row["request_id"]:
            tool = owner.loop.tools.resolve("get_circuit_operation")
            if tool is None or tool.annotations.get("readOnlyHint") is not True:
                raise ValueError("当前连接没有只读电路请求查询入口，请保留暂停并检查目标现场")
            with skill_call_context(session_id=owner.session_id, task_id=task_id,
                                    tool_call_id="reconcile_" + uuid.uuid4().hex) as execution:
                result = owner.loop.tools.execute(ToolCall("reconcile_" + uuid.uuid4().hex,
                    "get_circuit_operation", {"request_id": row["request_id"]}), context,
                    execution)
            return dict(status=result.status, summary=result.summary, data=result.data)
        requests = [event["payload"] for event in owner.journal.events()
                    if event["kind"] == "router.status" and event.get("task_id") == task_id
                    and event["payload"].get("tool_call_id") == row["id"]]
        broker = owner.bindings.broker
        read = getattr(broker, "request_receipt", None)
        if not requests or not callable(read):
            raise ValueError("该调用没有可查询的原请求回执；请根据卡片中的参数只读检查目标或产物")
        receipts = []
        for request in {item["request_id"]: item for item in requests}.values():
            try:
                receipt = read(context, request["request_id"], session_id=owner.session_id)
            except NeedsReconcile:
                archive = getattr(broker, "archived_receipt", None)
                if not callable(archive):
                    raise
                receipt = archive(owner.journal.root.parents[2], context,
                                  request["request_id"], session_id=owner.session_id)
            validate_router_receipt(receipt, request, context, owner.session_id)
            receipts.append(receipt)
        return receipts

    def resolve_interrupted(self, call_id, decision, *, expected_task, evidence=""):
        if decision not in {"completed", "not_executed", "no_calls", "uncertain"}:
            raise ValueError("请选择已完成、未执行或保持暂停")
        if not isinstance(evidence, str) or len(evidence) > 4000:
            raise ValueError("核对依据须为不超过 4000 字的文字")
        if decision in {"completed", "not_executed"} and not evidence.strip():
            raise ValueError("请填写核对依据（原请求回执、目标现状或产物），再确认结论")
        owner = self.controller
        with owner._lock:
            calls = self._calls(expected_task)
            if decision == "uncertain":
                owner.journal.append_session("session.reconcile_deferred", dict(
                    call_id=call_id, decision=decision, evidence=evidence or "用户选择保持暂停",
                    **audit_fields()), owner.current)
                owner.version += 1
                return dict(status="needs_reconcile", message="结果仍不确定，会话保持暂停；原调用不会重发")
            if decision == "no_calls":
                if calls.calls or call_id:
                    raise ValueError("本轮仍有待核对调用")
                evidence = "服务端核对原任务 journal：没有待核对的已派发调用"
            elif call_id not in calls.calls:
                raise ValueError("原任务没有这项已派发调用")
            if call_id in calls.decisions:
                if calls.decisions[call_id] != decision:
                    raise ValueError("该调用已有不同的核对结论")
            else:
                owner.journal.append_session("session.reconcile_decided",
                    dict(call_id=call_id, decision=decision, evidence=evidence.strip(),
                         **audit_fields()), owner.current)
                owner.version += 1
                calls.decisions[call_id] = decision
            if set(calls.calls) <= calls.decisions.keys():
                owner.acknowledge_interrupted()
                if owner.recovery is not None:
                    owner.recovery.continue_chat()
                owner.resume()
                return dict(status="cancelled", message="核对决定已记录，中断任务已结束；可继续发送新任务，原调用未重放")
            return dict(status="needs_reconcile", message="核对决定已记录；请继续核对其余调用，会话保持暂停")

    def auto_acknowledge_empty(self):
        """Close a task-only interruption that dispatched nothing."""
        owner = self.controller
        with owner._lock:
            if owner._task.get("status") != "needs_reconcile":
                return False
            calls = from_events(owner.journal.events(exclude=BULK_KINDS),
                                owner._task.get("id", ""))
            if calls.calls:
                return False
            self.authorize_completion(internal=True)
            event = owner.loop.acknowledge_interrupted()
            owner.history.event(event)
            owner._task = freeze(owner.loop.state.task)
            owner.paused = False
            owner.version += 1
            return True

    def authorize_completion(self, *, internal=False):
        """Every controller exit, including the old acknowledge command, checks the journal."""
        owner = self.controller
        calls = self._calls(owner._task.get("id", ""), allow_busy=internal)
        if set(calls.calls) - calls.decisions.keys():
            raise ValueError("仍有未核对的已派发调用；请在核对卡逐项查询并填写依据，不能直接放弃")
        owner.journal.append_session("session.reconcile_acknowledged", dict(
            decision="acknowledged", decisions=dict(calls.decisions),
            evidence=("原任务的所有已派发未决调用均有逐项核对决定" if calls.calls else
                      "服务端核对原任务 journal：没有待核对的已派发调用"),
            **audit_fields()), owner.current)
