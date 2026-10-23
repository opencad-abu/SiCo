"""Revalidate a Python task's captured target with durable internal read receipts."""

import json
import uuid

from ..core.contracts import BoundContext, NeedsReconcile, ToolCall, ToolResult
from ..core.target_validation import context_method, validate_target_receipt
from ..core.tools import tool_message


def validate(loop):
    if not loop.active or loop.stale or loop.cancelled():
        raise NeedsReconcile("Task no longer active")
    context = BoundContext.from_record(loop.state.context.record())
    call = ToolCall(uuid.uuid4().hex, context_method(context, loop.tools.schemas(), audit=True), {})
    # This host read is not a provider tool call. Do not splice messages into an
    # unfinished model tool group or replace its pending_calls bookkeeping.
    loop._event("tool.started", call.record())
    try:
        result = loop.execute_tool(call, context)
    except NeedsReconcile as exc:
        result = ToolResult("needs_reconcile", str(exc), exc.data)
    if not isinstance(result, ToolResult):
        raise NeedsReconcile("Target read returned no structured receipt")
    message = tool_message(call, result, loop.config.tool_result_chars, loop.journal.artifact)
    loop._event("tool.finished", {
        "id": call.id, "name": call.name, "result": json.loads(message["content"]),
    })
    validate_target_receipt(result.record())
