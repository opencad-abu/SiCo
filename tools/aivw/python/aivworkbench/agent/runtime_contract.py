"""Public runtime state and immutable result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math
from typing import Any, Mapping

from .context import MIN_CONTEXT_BYTES, redact_secrets
from .protocol import Action, AgentError, Event
from .value_codec import freeze, thaw

_ACTION_BUDGET_FIELDS = frozenset({
    "tokens", "tool_calls", "wall_seconds", "context_bytes", "turns",
    "simulation_cases", "simulation_seconds",
})
_CUMULATIVE_ACTION_BUDGET_FIELDS = frozenset({"tokens", "simulation_cases", "simulation_seconds"})

class AgentState(str):
    INIT = "INIT"
    INPUT_QUALIFICATION = "INPUT_QUALIFICATION"
    STRUCTURE_SNAPSHOT = "STRUCTURE_SNAPSHOT"
    TEMPLATE_MATCH = "TEMPLATE_MATCH"
    EXPERIMENT_PLAN = "EXPERIMENT_PLAN"
    SPECTRE_EXPERIMENT = "SPECTRE_EXPERIMENT"
    MEASURE = "MEASURE"
    MODEL_HYPOTHESIS = "MODEL_HYPOTHESIS"
    GENERATE_CANDIDATE = "GENERATE_CANDIDATE"
    STATIC_CHECK = "STATIC_CHECK"
    XCELIUM_CHECK = "XCELIUM_CHECK"
    CORRELATION = "CORRELATION"
    REFINE = "REFINE"
    QUALIFIED = "QUALIFIED"
    ANALOG_ISLAND = "ANALOG_ISLAND"
    FINISHED = "FINISHED"
    BLOCKED = "BLOCKED"
    INTERRUPTED = "INTERRUPTED"
    TIMEOUT = "TIMEOUT"
    UNKNOWN_SIDE_EFFECT = "UNKNOWN_SIDE_EFFECT"

    TERMINAL = frozenset({QUALIFIED, ANALOG_ISLAND, FINISHED, BLOCKED, INTERRUPTED, TIMEOUT, UNKNOWN_SIDE_EFFECT})


@dataclass(frozen=True)
class RuntimeConfig:
    """Runtime limits and persistence options."""

    max_turns: int = 16
    max_tool_calls: int = 16
    max_context_bytes: int = 64 * 1024
    max_context_items: int = 256
    turn_timeout_seconds: float = 60.0
    total_timeout_seconds: float = 15 * 60.0
    checkpoint_path: Path | None = None
    events_path: Path | None = None
    fsync_events: bool = True
    # M2 qualification may require providers to declare token and simulation
    # work before a domain action is authorized.  ``None`` keeps the M0
    # compatibility profile permissive while still validating any declared
    # budget fields against the action schema.
    max_action_tokens: int | None = None
    max_action_simulation_cases: int | None = None
    max_action_simulation_seconds: float | None = None
    max_total_action_tokens: int | None = None
    max_total_simulation_cases: int | None = None
    max_total_simulation_seconds: float | None = None
    required_action_budget_fields: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for name, value in (("max_turns", self.max_turns), ("max_tool_calls", self.max_tool_calls), ("max_context_bytes", self.max_context_bytes), ("max_context_items", self.max_context_items)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError("runtime %s must be an integer" % name)
        if self.max_turns <= 0 or self.max_tool_calls < 0:
            raise ValueError("runtime turn/tool limits must be positive")
        if self.max_context_bytes < MIN_CONTEXT_BYTES or self.max_context_items <= 0:
            raise ValueError("runtime context limits must be positive")
        if (
            not isinstance(self.turn_timeout_seconds, (int, float))
            or isinstance(self.turn_timeout_seconds, bool)
            or not math.isfinite(float(self.turn_timeout_seconds))
            or not isinstance(self.total_timeout_seconds, (int, float))
            or isinstance(self.total_timeout_seconds, bool)
            or not math.isfinite(float(self.total_timeout_seconds))
        ):
            raise ValueError("runtime timeouts must be numeric")
        if self.turn_timeout_seconds <= 0 or self.total_timeout_seconds <= 0:
            raise ValueError("runtime timeouts must be positive")
        for name, value in (
            ("max_action_tokens", self.max_action_tokens),
            ("max_action_simulation_cases", self.max_action_simulation_cases),
            ("max_total_action_tokens", self.max_total_action_tokens),
            ("max_total_simulation_cases", self.max_total_simulation_cases),
        ):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError("runtime %s must be a non-negative integer or null" % name)
        for name, value in (
            ("max_action_simulation_seconds", self.max_action_simulation_seconds),
            ("max_total_simulation_seconds", self.max_total_simulation_seconds),
        ):
            if value is not None and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or float(value) < 0
            ):
                raise ValueError("runtime %s must be a non-negative finite number or null" % name)
        if not isinstance(self.required_action_budget_fields, (set, frozenset, tuple, list)):
            raise ValueError("runtime required_action_budget_fields must be a set of names")
        required = frozenset(self.required_action_budget_fields)
        if any(not isinstance(item, str) or item not in _ACTION_BUDGET_FIELDS for item in required):
            raise ValueError("runtime required_action_budget_fields contains an unsupported field")
        object.__setattr__(self, "required_action_budget_fields", required)


@dataclass(frozen=True)
class RuntimeResult:
    run_id: str
    state: str
    status: str
    turns: int
    tool_calls: int
    events: tuple[Event, ...]
    last_error: AgentError | None = None
    final_action: Action | None = None
    checkpoint: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # ``RuntimeResult`` is returned to callers and may outlive the
        # runtime that produced it.  Freeze a redacted deep copy here so a
        # caller cannot mutate nested checkpoint data (or retain a reference
        # into the runtime's mutable checkpoint dictionary).
        object.__setattr__(
            self,
            "checkpoint",
            freeze(redact_secrets(self.checkpoint)),
        )

    @property
    def terminal(self) -> bool:
        return self.state in AgentState.TERMINAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state,
            "status": self.status,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "events": [item.to_dict() for item in self.events],
            "last_error": None if self.last_error is None else self.last_error.to_dict(),
            "final_action": None if self.final_action is None else self.final_action.to_dict(),
            "checkpoint": thaw(self.checkpoint),
        }


__all__ = ["AgentState", "RuntimeConfig", "RuntimeResult", "_ACTION_BUDGET_FIELDS", "_CUMULATIVE_ACTION_BUDGET_FIELDS"]
