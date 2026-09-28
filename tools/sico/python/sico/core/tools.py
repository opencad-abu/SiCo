"""Explicit tool assembly with preserved effect annotations and serialized execution."""

from __future__ import annotations

import json
import threading
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Callable

from .contracts import (
    BoundContext,
    CircuitCallError,
    NeedsReconcile,
    ToolCall,
    ToolResult,
    identifier,
    json_copy,
)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    validate: Callable[[dict], None]
    handler: Callable[[dict, BoundContext], ToolResult]
    execution_domain: str = "virtuoso"
    annotations: dict = field(
        default_factory=lambda: {"readOnlyHint": True, "destructiveHint": False}
    )
    # Host adapter contracts, never inferred from MCP hints or model arguments.
    effect: str = "unknown"
    input_dependencies: Callable[[dict, BoundContext], frozenset[str] | None] | None = None


# Compatibility for existing read adapters; shared writes use the general name.
ReadTool = Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._aliases: dict[str, str] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self.pdk_choice_validator = None
        self.pdk_update_validator = None
        self.target_choice_validator = None
        # Reports the action an answered existing-design decision already
        # authorizes for an observation, so no duplicate question is opened.
        self.settled_target_action = None

    def register(self, tool: Tool) -> None:
        identifier(tool.name)
        if tool.name in self._tools:
            raise ValueError("duplicate tool name")
        if tool.schema.get("type") != "object":
            raise ValueError("tool schema must describe an object")
        self._tools[tool.name] = tool

    def alias(self, name: str, target: str) -> None:
        """Execute one tool under a recorded name; aliases are never advertised."""
        identifier(name)
        if target not in self._tools:
            raise ValueError("Alias target is not registered")
        self._aliases[name] = target

    def resolve(self, name: str) -> Tool | None:
        return self._tools.get(name) or self._tools.get(self._aliases.get(name, ""))

    def schemas(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": json_copy(t.schema),
                "annotations": json_copy(t.annotations),
            }
            for t in self._tools.values()
        ]

    def execute(self, call: ToolCall, context: BoundContext) -> ToolResult:
        tool = self.resolve(call.name)
        if tool is None:
            return ToolResult("tool_error", "Tool is not registered")
        try:
            inputs = json_copy(call.inputs)
            tool.validate(inputs)
        except (ValueError, TypeError) as exc:
            return ToolResult("invalid_arguments", str(exc))
        with self._guard:
            lock = self._locks.setdefault(context.instance_id, threading.Lock())
        try:
            # Read-only does not mean parallel-safe in a live SKILL process.
            # Broker-backed registries use the instance FIFO, including reads.
            with nullcontext() if getattr(self, "routed", False) else lock:
                # A queued tool may outlive its model turn while waiting for
                # another handler. Recheck before entering any tool code.
                from ..transport.broker import _CALL_CONTEXT
                cancelled = _CALL_CONTEXT.get().get("cancelled")
                if cancelled and cancelled():
                    return ToolResult("cancelled_before_start", "任务已停止，工具未执行")
                before_start = _CALL_CONTEXT.get().get("before_start")
                if before_start is not None:
                    blocked = before_start()
                    if blocked is not None:
                        return blocked
                return tool.handler(inputs, context)
        except CircuitCallError as exc:
            return ToolResult(exc.code, str(exc), exc.data)
        except NeedsReconcile:
            raise
        except Exception as exc:
            code = getattr(exc, "code", None)
            if isinstance(code, str) and code in {
                "skill_queued", "skill_blocked_unknown", "skill_response_pending", "skill_timeout",
                "skill_queue_timeout", "skill_queue_full", "cancelled_before_start",
                "router_unavailable",
                "waiting_user",
            }:
                return ToolResult(code, str(exc), {"code": code})
            return ToolResult("tool_error", f"Tool failed ({type(exc).__name__})")


def no_arguments(inputs: dict) -> None:
    if not isinstance(inputs, dict) or inputs:
        raise ValueError("This tool accepts an empty JSON object only")


def tool_message(
    call: ToolCall, result: ToolResult, limit: int, save_artifact: Callable[[bytes], dict]
) -> dict:
    record = result.record()
    encoded = json.dumps(record, ensure_ascii=False, allow_nan=False)
    if len(encoded) > limit:
        artifact = save_artifact(encoded.encode("utf-8"))
        record = {
            "status": result.status,
            "summary": result.summary[:1000],
            "untrusted": True,
            "truncated": True,
            "artifact": artifact,
            "preview": encoded[: max(0, limit - 2000)],
        }
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": json.dumps(record, ensure_ascii=False, allow_nan=False),
    }
