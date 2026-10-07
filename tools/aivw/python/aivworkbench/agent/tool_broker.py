"""Registration, schema validation, and serialized execution of domain tools."""

from __future__ import annotations

import threading
from typing import Any, Callable, Mapping

from .context import redact_text
from .policy import AgentPolicy
from .protocol import (
    Action,
    ActionKind,
    AgentError,
    ErrorCode,
    ProtocolError,
)
from .value_codec import thaw
from .broker_contract import BrokerResult, ToolResult, ToolSpec
from . import broker_execution
from . import broker_checkpoint
from .broker_validation import (
    _clone_schema,
    _tool_for_action,
    _validate_capabilities,
    _validate_schema,
)


ToolHandler = Callable[[Mapping[str, Any]], "ToolResult"]




class ToolBroker:
    """A single-flight broker with idempotency and fail-closed validation."""

    def __init__(self, policy: AgentPolicy, *, source_generation: str, template_lock: str | None = None) -> None:
        self.policy = policy
        self.source_generation = source_generation
        self.template_lock = template_lock
        self._tools: dict[str, ToolSpec] = {}
        self._actions: dict[str, str] = {}
        self._results: dict[str, BrokerResult] = {}
        self._lock = threading.RLock()
        self._active = False
        self._in_flight: dict[str, Any] | None = None

    @property
    def in_flight(self) -> Mapping[str, Any] | None:
        """Return a defensive copy of the active action marker for recovery."""
        with self._lock:
            return None if self._in_flight is None else thaw(self._in_flight)

    def register(self, spec: ToolSpec) -> None:
        if not isinstance(spec, ToolSpec):
            raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "tool specification must be a ToolSpec")
        if not isinstance(spec.name, str) or not spec.name or spec.name != spec.name.strip() or any(ch.isspace() for ch in spec.name):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool name is invalid")
        if spec.handler is None or not callable(spec.handler):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool handler is required", {"tool": spec.name})
        _validate_capabilities(spec.capabilities)
        if spec.name in self._tools:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool is already registered", {"tool": spec.name})
        try:
            _validate_schema(spec.schema)
            detached_schema = _clone_schema(spec.schema)
        except ProtocolError:
            raise
        except (TypeError, ValueError, OSError, RuntimeError, RecursionError) as exc:
            raise ProtocolError(
                ErrorCode.TOOL_SCHEMA_INVALID,
                "tool schema is invalid",
                {"tool": spec.name, "detail": redact_text(str(exc))},
            ) from exc
        self._tools[spec.name] = ToolSpec(
            name=spec.name,
            schema=detached_schema,
            handler=spec.handler,
            side_effect=spec.side_effect,
            capabilities=frozenset(spec.capabilities),
        )

    def register_tool(
        self,
        name: str,
        handler: ToolHandler,
        *,
        schema: Mapping[str, Any] | None = None,
        side_effect: bool = False,
        capabilities: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        if schema is not None and not isinstance(schema, Mapping):
            raise ProtocolError(ErrorCode.TOOL_SCHEMA_INVALID, "tool schema must be an object")
        if not isinstance(side_effect, bool):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool side_effect must be boolean")
        try:
            normalized_capabilities = frozenset(capabilities)
        except (TypeError, ValueError) as exc:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool capabilities must be a set of names") from exc
        self.register(ToolSpec(name, dict(schema or {}), handler, side_effect, normalized_capabilities))

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "schema": _clone_schema(spec.schema),
                "side_effect": spec.side_effect,
                "capabilities": sorted(spec.capabilities),
            }
            for name, spec in sorted(self._tools.items())
        ]

    def execute(self, *args, **kwargs):
        return broker_execution.execute(self, *args, **kwargs)

    def validate(self, action: Action | Mapping[str, Any]) -> AgentError | None:
        """Validate an action without entering the single-flight side-effect boundary."""
        try:
            candidate = action if isinstance(action, Action) else Action.from_dict(action)
            self.policy.validate_action(candidate, source_generation=self.source_generation, template_lock=self.template_lock)
            if candidate.kind in (ActionKind.FINISH.value, ActionKind.BLOCKED.value):
                if "tool" in candidate.params:
                    raise ProtocolError(
                        ErrorCode.TOOL_NOT_ALLOWED,
                        "terminal actions cannot select a domain tool",
                    )
            else:
                _tool_for_action(candidate, self.policy, self._tools)
        except ProtocolError as exc:
            return exc.to_error()
        return None

    def invoke(self, action: Action | Mapping[str, Any]) -> BrokerResult:
        return self.execute(action)

    def _execute_single(self, *args, **kwargs):
        return broker_execution._execute_single(self, *args, **kwargs)

    def _unknown_side_effect_result(self, *args, **kwargs):
        return broker_execution._unknown_side_effect_result(self, *args, **kwargs)

    @property
    def completed_action_ids(self) -> frozenset[str]:
        return frozenset(item.action.action_id for item in self._results.values())

    def export_results(self) -> dict[str, dict[str, Any]]:
        return {key: thaw(value.to_dict()) for key, value in self._results.items()}

    def export_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "actions": dict(self._actions),
                "results": self.export_results(),
                "in_flight": None if self._in_flight is None else thaw(self._in_flight),
            }

    def restore_state(self, *args, **kwargs):
        return broker_checkpoint.restore_state(self, *args, **kwargs)






























__all__ = ["BrokerResult", "ToolBroker", "ToolResult", "ToolSpec"]
