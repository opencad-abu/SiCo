"""Task-local metadata and cancellation scope for a native call."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from ..core.contracts import ToolExecution

_CALL_CONTEXT = ContextVar("skill_call_context", default={})

@contextmanager
def skill_call_context(**fields):
    from cadai.pdk_pool import collection_scope

    token = _CALL_CONTEXT.set(fields)
    try:
        with collection_scope(fields.get("cancelled")):
            yield ToolExecution(
                cancelled=fields.get("cancelled"),
                before_start=fields.get("before_start"),
            )
    finally:
        _CALL_CONTEXT.reset(token)
