"""Deterministic initial context query, with normal task/tool evidence and no model turn."""

from __future__ import annotations

import json
import uuid

from .contracts import Cancelled, NeedsReconcile, ToolCall, ToolResult
from .tools import tool_message

INITIAL_PROMPT = "你好，获取一下当前窗口的信息"


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
        loop._check_cancel(cancelled)
        stale = None
        try:
            result = loop.execute_tool(call, context, cancelled)
        except NeedsReconcile as exc:
            stale = exc
            result = ToolResult("needs_reconcile", str(exc))
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
        loop._check_cancel(cancelled)
        if stale:
            raise stale
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
    loop._check_cancel(cancelled)
    stale = None
    try:
        result = loop.execute_tool(call, context, cancelled)
    except NeedsReconcile as exc:
        stale = exc
        result = ToolResult("needs_reconcile", str(exc))
    message = tool_message(call, result, loop.config.tool_result_chars, loop.journal.artifact)
    loop.state.messages.append(message)
    loop.state.task["pending_calls"] = []
    yield loop._event(
        "tool.finished",
        {"id": call.id, "name": call.name, "result": json.loads(message["content"])},
    )
    if stale:
        raise stale
    if result.status != "ok":
        raise NeedsReconcile("Task source context unavailable; reconcile before continuing")
