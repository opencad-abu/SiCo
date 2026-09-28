"""Exact-request observations and explicit decisions outside the paused task queue."""

import json
import uuid

from ..core.contracts import NeedsReconcile, ToolCall
from ..transport.broker import skill_call_context
from .reconcile_calls import from_events
from .router_receipt import validate_router_receipt


class SessionReconciliation:
    def __init__(self, controller):
        self.controller = controller

    def _calls(self, expected_task):
        owner = self.controller
        if (owner.busy or owner.closing or owner._shutdown.is_set()
                or owner._task.get("id") != expected_task
                or owner._task.get("status") != "needs_reconcile"):
            raise ValueError("原中断任务已变化，请刷新后核对")
        return from_events(owner.journal.events(), expected_task)

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
                dict(call_id=call_id, text=text), context)
            owner.version += 1
        return None

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
                                    tool_call_id="reconcile_" + uuid.uuid4().hex):
                result = owner.loop.tools.execute(ToolCall("reconcile_" + uuid.uuid4().hex,
                    "get_circuit_operation", {"request_id": row["request_id"]}), context)
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

    def resolve_interrupted(self, call_id, decision, *, expected_task):
        if decision not in {"completed", "not_executed", "no_calls"}:
            raise ValueError("请选择已完成、未执行或保持暂停")
        owner = self.controller
        with owner._lock:
            calls = self._calls(expected_task)
            if decision == "no_calls":
                if calls.calls or call_id:
                    raise ValueError("本轮仍有待核对调用")
            elif call_id not in calls.calls:
                raise ValueError("原任务没有这项已派发调用")
            elif call_id in calls.decisions:
                if calls.decisions[call_id] != decision:
                    raise ValueError("该调用已有不同的核对结论")
            else:
                owner.journal.append_session("session.reconcile_decided",
                    dict(call_id=call_id, decision=decision), owner.current)
                owner.version += 1
                calls.decisions[call_id] = decision
            if set(calls.calls) <= calls.decisions.keys():
                owner.acknowledge_interrupted()
                if owner.recovery is not None:
                    owner.recovery.release()
                owner.resume()
