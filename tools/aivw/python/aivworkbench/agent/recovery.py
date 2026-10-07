"""Crash, timeout, disconnect, and unknown-side-effect handling."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .protocol import AgentError, ErrorCode


class RecoveryKind(str, Enum):
    RETRYABLE = "retryable"
    INTERRUPTED = "interrupted"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"
    FATAL = "fatal"


@dataclass(frozen=True)
class RecoveryDecision:
    kind: RecoveryKind
    code: str
    message: str
    retry_allowed: bool = False
    preserve_checkpoint: bool = True
    details: Mapping[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.details is None:
            object.__setattr__(self, "details", {})

    def to_error(self) -> AgentError:
        return AgentError(self.code, self.message, dict(self.details or {}))


class RecoveryManager:
    """Centralizes conservative retry decisions.

    A timeout or disconnect after a mutating tool began is always unknown.  A
    provider call that has no side effect can be retried only when the caller
    explicitly supplies remaining budget.
    """

    def classify(
        self,
        exc: BaseException,
        *,
        operation: str,
        side_effect_started: bool = False,
        provider: bool = False,
    ) -> RecoveryDecision:
        name = type(exc).__name__.lower()
        text = str(exc) or name
        if side_effect_started or "unknown" in name or "sideeffect" in name:
            return RecoveryDecision(RecoveryKind.UNKNOWN_SIDE_EFFECT, ErrorCode.UNKNOWN_SIDE_EFFECT.value, text, False, True, {"operation": operation})
        if isinstance(exc, (TimeoutError,)) or "timeout" in name or "timed out" in text.lower():
            code = ErrorCode.PROVIDER_TIMEOUT.value if provider else ErrorCode.UNKNOWN_SIDE_EFFECT.value
            kind = RecoveryKind.PROVIDER_UNAVAILABLE if provider else RecoveryKind.UNKNOWN_SIDE_EFFECT
            return RecoveryDecision(kind, code, text, False, True, {"operation": operation})
        if "disconnect" in name or "brokenpipe" in name or "connection" in name:
            code = ErrorCode.PROVIDER_DISCONNECTED.value if provider else ErrorCode.UNKNOWN_SIDE_EFFECT.value
            kind = RecoveryKind.PROVIDER_UNAVAILABLE if provider else RecoveryKind.UNKNOWN_SIDE_EFFECT
            return RecoveryDecision(kind, code, text, False, True, {"operation": operation})
        if provider:
            return RecoveryDecision(RecoveryKind.PROVIDER_UNAVAILABLE, ErrorCode.PROVIDER_UNAVAILABLE.value, text, False, True, {"operation": operation})
        return RecoveryDecision(RecoveryKind.FATAL, ErrorCode.INTERNAL_ERROR.value, text, False, True, {"operation": operation})

    def provider_unavailable(self, message: str = "model provider is unavailable") -> RecoveryDecision:
        return RecoveryDecision(RecoveryKind.PROVIDER_UNAVAILABLE, ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value, message, False, True)

    def interrupted(self, message: str = "agent was interrupted") -> RecoveryDecision:
        return RecoveryDecision(RecoveryKind.INTERRUPTED, ErrorCode.INTERRUPTED.value, message, False, True)

    def unknown_side_effect(self, operation: str, message: str = "side effect state is unknown") -> RecoveryDecision:
        return RecoveryDecision(RecoveryKind.UNKNOWN_SIDE_EFFECT, ErrorCode.UNKNOWN_SIDE_EFFECT.value, message, False, True, {"operation": operation})

    @staticmethod
    def can_resume(decision: RecoveryDecision) -> bool:
        return decision.kind not in {RecoveryKind.UNKNOWN_SIDE_EFFECT, RecoveryKind.FATAL}


__all__ = ["RecoveryDecision", "RecoveryKind", "RecoveryManager"]
