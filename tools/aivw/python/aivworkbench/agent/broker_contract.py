"""Tool and broker result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .context import ArtifactLocator, redact_secrets
from .protocol import ErrorCode, ProtocolError
from .value_codec import freeze, thaw
from .broker_validation import _FORBIDDEN_VERDICT_FIELDS, _ensure_json_safe, _reject_result_verdict
from .protocol import Action, AgentError

ToolHandler = Callable[[Mapping[str, Any]], "ToolResult"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema: Mapping[str, Any] = field(default_factory=dict)
    handler: ToolHandler | None = None
    side_effect: bool = False
    capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ToolResult:
    status: str
    summary: Mapping[str, Any] = field(default_factory=dict)
    outputs: Mapping[str, Any] = field(default_factory=dict)
    artifacts: tuple[Mapping[str, Any], ...] = ()
    source_generation: str | None = None
    side_effect: bool = False
    deterministic_gate: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, str) or not self.status or len(self.status) > 64:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool result status is invalid")
        if not isinstance(self.summary, Mapping) or not isinstance(self.outputs, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool result maps are invalid")
        if not isinstance(self.artifacts, (tuple, list)):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool result artifacts must be a sequence")
        normalized_artifacts = []
        for item in self.artifacts:
            if not isinstance(item, Mapping):
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool result artifact must be an object")
            # Results cross a persistence and provider boundary.  Keep the
            # artifact contract narrow and locator-shaped so an arbitrary
            # absolute path can never enter an event/checkpoint payload.
            locator = ArtifactLocator.from_dict(item)
            # Detach the locator from the handler's mutable mapping.  Frozen
            # dataclasses do not freeze nested dictionaries by themselves.
            normalized_artifacts.append(MappingProxyType(dict(locator.to_dict())))
        object.__setattr__(self, "artifacts", tuple(normalized_artifacts))
        if self.source_generation is not None and (
            not isinstance(self.source_generation, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,255}", self.source_generation)
        ):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool result source_generation is invalid")
        if not isinstance(self.side_effect, bool):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool result side_effect must be boolean")
        if self.deterministic_gate is not None and (
            not isinstance(self.deterministic_gate, str) or not self.deterministic_gate
        ):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool result deterministic_gate is invalid")
        # Tool output is evidence only.  A handler may attach a gate name, but
        # it cannot smuggle an agent verdict through summary/output fields.
        _reject_result_verdict(self.summary, "summary")
        _reject_result_verdict(self.outputs, "outputs")
        safe_summary = freeze(redact_secrets(self.summary))
        safe_outputs = freeze(redact_secrets(self.outputs))
        object.__setattr__(self, "summary", safe_summary)
        object.__setattr__(self, "outputs", safe_outputs)
        _ensure_json_safe(safe_summary, "summary")
        _ensure_json_safe(safe_outputs, "outputs")

    def to_dict(self) -> dict[str, Any]:
        value = {
            "status": self.status,
            "summary": thaw(self.summary),
            "outputs": thaw(self.outputs),
            "artifacts": [thaw(item) for item in self.artifacts],
            "source_generation": self.source_generation,
            "side_effect": self.side_effect,
        }
        if self.deterministic_gate is not None:
            value["deterministic_gate"] = self.deterministic_gate
        return value


@dataclass(frozen=True)
class BrokerResult:
    action: Action
    tool: str | None
    result: ToolResult | None = None
    error: AgentError | None = None
    replayed: bool = False
    unknown_side_effect: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.to_dict(),
            "action_id": self.action.action_id,
            "tool": self.tool,
            "result": None if self.result is None else self.result.to_dict(),
            "error": None if self.error is None else self.error.to_dict(),
            "replayed": self.replayed,
            "unknown_side_effect": self.unknown_side_effect,
        }


__all__ = ["ToolSpec", "ToolResult", "BrokerResult", "_FORBIDDEN_VERDICT_FIELDS"]
