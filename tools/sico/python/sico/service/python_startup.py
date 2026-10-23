"""Deterministic initial context query, with normal task/tool evidence and no model turn."""

from __future__ import annotations

import json
import uuid

from ..core.contracts import Cancelled, NeedsReconcile, ToolCall, ToolResult
from ..core.tools import tool_message

from ..core.startup import INITIAL_PROMPT


def _context_result(loop, call, context, cancelled):
    try:
        loop._check_cancel(cancelled)
    except Cancelled:
        return ToolResult("cancelled_before_start", "任务已停止，工具未执行")
    try:
        return loop.execute_tool(call, context, cancelled)
    except NeedsReconcile as exc:
        return ToolResult("needs_reconcile", str(exc), exc.data)


def initial_context(loop, cancelled, *, context, input_id):
    loop._begin(INITIAL_PROMPT, context, input_id, "startup")
    try:
        yield loop._event(
            "task.started",
            {
                "context": context.record(),
                "text": INITIAL_PROMPT,
                "input_id": input_id,
                "origin": "startup",
            },
        )
        loop._check_cancel(cancelled)
        yield loop._activity("context", "读取当前设计上下文")
        method = (
            "get_project_context"
            if "get_project_context" in context.snapshot.get("capabilities", [])
            else "get_entry_context"
            if "get_entry_context" in context.snapshot.get("capabilities", [])
            else "get_context"
        )
        call = ToolCall(uuid.uuid4().hex, method, {})
        loop.state.messages.append(
            {"role": "assistant", "content": "", "tool_calls": [call.record()]}
        )
        loop.state.task["pending_calls"] = [call.record()]
        yield loop._event("tool.started", call.record())
        result = _context_result(loop, call, context, cancelled)
        message = tool_message(call, result, loop.config.tool_result_chars, loop.journal.artifact)
        loop.state.messages.append(message)
        loop.state.task["pending_calls"] = []
        yield loop._event(
            "tool.finished",
            {
                "id": call.id,
                "name": call.name,
                "result": json.loads(message["content"]),
            },
        )
        if result.status == "needs_reconcile":
            raise NeedsReconcile(result.summary, data=result.data)
        loop._check_cancel(cancelled)
        yield loop._finish("completed" if result.status == "ok" else "failed", result.summary)
    except Cancelled:
        loop._close_pending()
        yield loop._finish("cancelled", "Initial context query cancelled")
    except NeedsReconcile as exc:
        yield loop._finish("needs_reconcile", str(exc))
    except Exception as exc:
        yield loop._finish(
            "needs_reconcile", f"Initial context query interrupted ({type(exc).__name__})"
        )
    finally:
        if loop.state.task["status"] == "executing":
            loop._finish("needs_reconcile", "Initial context consumer disconnected")


def task_context(loop, cancelled):
    """Attach fresh source facts for this queued task, never replace its captured identity."""
    context = loop.state.context
    capabilities = context.snapshot.get("capabilities", [])
    if not {"get_project_context", "get_entry_context"}.intersection(capabilities):
        return
    loop._check_cancel(cancelled)
    yield loop._activity("context", "刷新任务上下文")
    method = "get_project_context" if "get_project_context" in capabilities else "get_entry_context"
    call = ToolCall(uuid.uuid4().hex, method, {})
    loop.state.messages.append({"role": "assistant", "content": "", "tool_calls": [call.record()]})
    loop.state.task["pending_calls"] = [call.record()]
    yield loop._event("tool.started", call.record())
    result = _context_result(loop, call, context, cancelled)
    message = tool_message(call, result, loop.config.tool_result_chars, loop.journal.artifact)
    loop.state.messages.append(message)
    loop.state.task["pending_calls"] = []
    yield loop._event(
        "tool.finished",
        {"id": call.id, "name": call.name, "result": json.loads(message["content"])},
    )
    if result.status == "needs_reconcile":
        raise NeedsReconcile(result.summary, data=result.data)
    loop._check_cancel(cancelled)
    if result.status != "ok":
        raise NeedsReconcile("Task source context unavailable; reconcile before continuing")
