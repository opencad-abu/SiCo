"""Versioned, JSON-only agent contracts adapted from aDesigner event/result models."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from cadai.json_values import json_copy

PROVIDER_CONTRACT = "agent_provider.v1"
CONTEXT_CONTRACT = "virtuoso_context.v1"
TASK_CONTRACT = "agent_task.v1"
ID = re.compile(r"^[a-zA-Z0-9_-]{1,96}$")
TERMINAL = frozenset({"completed", "failed", "cancelled"})


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("invalid identifier")
    return value


@dataclass(frozen=True)
class BoundContext:
    instance_id: str
    generation: str
    target_id: str
    snapshot: dict

    def __post_init__(self) -> None:
        for value in (self.instance_id, self.generation, self.target_id):
            identifier(value)
        if not isinstance(self.snapshot, dict):
            raise ValueError("context snapshot must be an object")
        object.__setattr__(self, "snapshot", json_copy(self.snapshot))

    def record(self) -> dict:
        return {
            "contract": CONTEXT_CONTRACT,
            "instance_id": self.instance_id,
            "generation": self.generation,
            "target_id": self.target_id,
            "snapshot": json_copy(self.snapshot),
        }

    @classmethod
    def from_record(cls, value: dict) -> BoundContext:
        if value.get("contract") != CONTEXT_CONTRACT:
            raise ValueError("unsupported context contract")
        return cls(value["instance_id"], value["generation"], value["target_id"], value["snapshot"])


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    inputs: dict

    def record(self) -> dict:
        identifier(self.id)
        identifier(self.name)
        if not isinstance(self.inputs, dict):
            raise ValueError("tool arguments must be a JSON object")
        return {"id": self.id, "name": self.name, "input": json_copy(self.inputs)}


@dataclass(frozen=True)
class ToolResult:
    status: str = "ok"
    summary: str = ""
    data: Any = None

    def record(self) -> dict:
        return {
            "status": self.status,
            "summary": self.summary,
            "data": json_copy(self.data),
            "untrusted": True,
        }


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ModelTurn:
    text: str
    calls: tuple[ToolCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    # Protocol-only content (e.g. Anthropic signed blocks); never a GUI event.
    blocks: tuple[dict, ...] = ()


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, code: str = "provider_error"):
        super().__init__(message)
        self.retryable = retryable
        self.code = code


class Cancelled(RuntimeError):
    pass


class NeedsReconcile(RuntimeError):
    def __init__(self, message, *, data=None):
        super().__init__(message)
        self.data = json_copy(data) if data is not None else {}
        self.code = self.data.get("code", "needs_reconcile")


class CircuitCallError(RuntimeError):
    """A diagnosed circuit failure that leaves the captured context usable."""

    def __init__(self, code, message, *, data=None):
        super().__init__(message)
        self.code = code
        self.data = {**(json_copy(data) if data is not None else {}), "code": code}


class Provider(Protocol):
    def stream(
        self, system: str, messages: list[dict], tools: list[dict], cancelled: Callable[[], bool]
    ) -> Iterator[TextDelta | ModelTurn]: ...


@dataclass(frozen=True)
class RunConfig:
    max_turns: int = 100
    max_calls_per_turn: int = 16
    max_retries: int = 2
    retry_delay: float = 0.2
    # Conversation compaction budget; provider context limits also cover tools/system.
    context_chars: int = 240_000
    max_output_chars: int = 128_000
    tool_result_chars: int = 8_000

    def __post_init__(self) -> None:
        for name in (
            "max_turns",
            "max_calls_per_turn",
            "context_chars",
            "max_output_chars",
            "tool_result_chars",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.max_retries < 0 or self.retry_delay < 0:
            raise ValueError("retry limits cannot be negative")


@dataclass
class RunState:
    context: BoundContext
    messages: list[dict] = field(default_factory=list)
    task: dict = field(default_factory=dict)

    def record(self) -> dict:
        return json_copy(
            {"context": self.context.record(), "messages": self.messages, "task": self.task}
        )

    @classmethod
    def from_record(cls, record: dict) -> RunState:
        messages, task = record["messages"], record["task"]
        if not isinstance(messages, list) or not isinstance(task, dict):
            raise ValueError("Invalid session state")
        if task:
            identifier(task["id"])
            if task["status"] not in TERMINAL | {"executing", "needs_reconcile"}:
                raise ValueError("Invalid task status")
            if not isinstance(task.get("pending_calls"), list):
                raise ValueError("Invalid pending calls")
        for message in messages:
            if (
                not isinstance(message, dict)
                or message.get("role") not in {"user", "assistant", "tool"}
                or not isinstance(message.get("content"), str)
            ):
                raise ValueError("Invalid history message")
        return cls(
            BoundContext.from_record(record["context"]),
            json_copy(messages),
            json_copy(task),
        )
