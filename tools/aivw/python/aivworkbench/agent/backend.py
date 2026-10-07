"""Provider contracts for the self-contained AIVW agent.

Providers are intentionally passive.  They can propose an :class:`Action` or
report provider health, but they do not own tools, files, processes, or
verification verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from typing import Any, Iterable, Mapping, Protocol

from .protocol import (
    Action,
    AgentError,
    EventType,
    PROTOCOL_VERSION,
    ProtocolError,
    ErrorCode,
)
from .value_codec import ensure_json, freeze, redact, redact_text, reject_verdict_fields, thaw


class ProviderUnavailable(RuntimeError):
    """Raised when a provider cannot produce a response in the current profile."""

    def __init__(self, code: str = "MODEL_PROVIDER_UNAVAILABLE", message: str = "model provider is unavailable"):
        self.code = str(code.value if isinstance(code, ErrorCode) else code)
        # Provider messages cross the runtime event/checkpoint boundary.  The
        # runtime's AgentError performs the final recursive redaction; avoid
        # retaining arbitrary non-text exception objects here.
        super().__init__(redact_text(str(message)))


@dataclass(frozen=True)
class ProviderRequest:
    run_id: str
    turn_id: str
    source_generation: str
    context: Mapping[str, Any]
    template_lock: str | None = None
    budget: Mapping[str, Any] = field(default_factory=dict)
    previous_result: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    protocol_version: str = PROTOCOL_VERSION

    _FIELDS = frozenset(
        {
            "protocol_version",
            "run_id",
            "turn_id",
            "source_generation",
            "context",
            "template_lock",
            "budget",
            "previous_result",
            "metadata",
        }
    )
    _REQUIRED = frozenset({"run_id", "turn_id", "source_generation", "context"})

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(ErrorCode.BUNDLE_PROTOCOL_MISMATCH, "provider request protocol version is unsupported")
        for name in ("run_id", "turn_id", "source_generation"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "provider request %s is required" % name)
        if not isinstance(self.context, Mapping) or not isinstance(self.budget, Mapping) or not isinstance(self.metadata, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "provider request maps are invalid")
        if self.previous_result is not None and not isinstance(self.previous_result, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "provider request previous_result is invalid")
        for name, value in (("budget", self.budget),):
            for key, raw in value.items():
                if not isinstance(key, str) or not isinstance(raw, (int, float)) or isinstance(raw, bool) or not math.isfinite(float(raw)) or float(raw) < 0:
                    raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "provider request %s contains an invalid numeric field" % name, {"field": str(key)})
        safe_context = freeze(redact(self.context))
        safe_budget = freeze(redact(self.budget))
        safe_previous = None if self.previous_result is None else freeze(redact(self.previous_result))
        safe_metadata = freeze(redact(self.metadata))
        object.__setattr__(self, "context", safe_context)
        object.__setattr__(self, "budget", safe_budget)
        object.__setattr__(self, "previous_result", safe_previous)
        object.__setattr__(self, "metadata", safe_metadata)
        try:
            ensure_json(self.to_dict())
        except ProtocolError as exc:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "provider request contains non-JSON data", exc.details) from exc
        except (TypeError, ValueError) as exc:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "provider request contains non-JSON data", {"detail": str(exc)}) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "source_generation": self.source_generation,
            "context": thaw(self.context),
            "template_lock": self.template_lock,
            "budget": thaw(self.budget),
            "previous_result": None if self.previous_result is None else thaw(self.previous_result),
            "metadata": thaw(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ProviderRequest":
        if not isinstance(value, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "provider request must be an object")
        unknown = sorted(
            str(key)
            for key in value
            if not isinstance(key, str) or key not in cls._FIELDS
        )
        if unknown:
            raise ProtocolError(
                ErrorCode.UNKNOWN_FIELD,
                "provider request contains unknown fields",
                {"fields": unknown},
            )
        missing = sorted(cls._REQUIRED - set(value))
        if missing:
            raise ProtocolError(
                ErrorCode.INVALID_ARGUMENTS,
                "provider request is missing required fields",
                {"fields": missing},
            )
        return cls(
            run_id=value.get("run_id"),
            turn_id=value.get("turn_id"),
            source_generation=value.get("source_generation"),
            context=value.get("context"),
            template_lock=value.get("template_lock"),
            budget=value.get("budget", {}),
            previous_result=value.get("previous_result"),
            metadata=value.get("metadata", {}),
            protocol_version=value.get("protocol_version", PROTOCOL_VERSION),
        )


@dataclass(frozen=True)
class ProviderResponse:
    """One provider response for a turn."""

    action: Action | None = None
    error: AgentError | None = None
    provider: str = "unknown"
    model_id: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action is None and self.error is None:
            raise ProtocolError(ErrorCode.INVALID_ACTION, "provider response needs an action or an error")
        if self.action is not None and self.error is not None:
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "provider response cannot contain both action and error")
        if self.action is not None and not isinstance(self.action, Action):
            raise ProtocolError(ErrorCode.INVALID_ACTION, "provider response action is invalid")
        if self.error is not None and not isinstance(self.error, AgentError):
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "provider response error is invalid")
        for name, value in (("usage", self.usage), ("raw_metadata", self.raw_metadata)):
            if not isinstance(value, Mapping):
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "provider response %s must be an object" % name)
            safe_value = freeze(redact(value))
            object.__setattr__(self, name, safe_value)
            try:
                ensure_json(safe_value, name)
            except ProtocolError as exc:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "provider response %s is not JSON-safe" % name, exc.details) from exc
            except (TypeError, ValueError) as exc:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "provider response %s is not JSON-safe" % name, {"detail": str(exc)}) from exc
            reject_verdict_fields(safe_value, name, error_code=ErrorCode.INVALID_ARGUMENTS.value, message="provider response contains a verdict field", cycle_message="provider response contains a cyclic object")
        if self.action is not None:
            # Action validates its parameter payload, but callers may hand a
            # custom Action-like object through a subclass.  Re-check the
            # canonical serialization before accepting the response.
            ensure_json(self.action.to_dict(), "action")
        if not isinstance(self.provider, str) or not self.provider or len(self.provider) > 128:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "provider response provider is invalid")
        if self.model_id is not None and (not isinstance(self.model_id, str) or not self.model_id or len(self.model_id) > 256):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "provider response model_id is invalid")

    @classmethod
    def from_action(cls, action: Action, provider: str = "unknown", **kwargs: Any) -> "ProviderResponse":
        return cls(action=action, provider=provider, **kwargs)

    @classmethod
    def unavailable(cls, provider: str, message: str = "model provider is unavailable") -> "ProviderResponse":
        return cls(error=AgentError("MODEL_PROVIDER_UNAVAILABLE", message), provider=provider)


@dataclass(frozen=True)
class ProviderEvent:
    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    provider: str = "unknown"

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, str) or not self.event_type or len(self.event_type) > 128:
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "provider event type is invalid")
        if not isinstance(self.payload, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "provider event payload must be an object")
        safe_payload = freeze(redact(self.payload))
        object.__setattr__(self, "payload", safe_payload)
        ensure_json(safe_payload)
        if not isinstance(self.provider, str) or not self.provider or len(self.provider) > 128:
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "provider event provider is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {"event_type": self.event_type, "payload": thaw(self.payload), "provider": self.provider}


@dataclass(frozen=True)
class ProviderSession:
    session_id: str
    provider: str
    protocol_version: str = PROTOCOL_VERSION
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (("session_id", self.session_id), ("provider", self.provider), ("protocol_version", self.protocol_version)):
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "provider session %s is invalid" % name)
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(ErrorCode.BUNDLE_PROTOCOL_MISMATCH, "provider session protocol version is unsupported")
        if not isinstance(self.metadata, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "provider session metadata must be an object")
        safe_metadata = freeze(redact(self.metadata))
        object.__setattr__(self, "metadata", safe_metadata)
        ensure_json(safe_metadata)

class AgentProvider(Protocol):
    """Minimal synchronous provider protocol used by :class:`AgentRuntime`."""

    name: str

    def start(self, request: ProviderRequest) -> ProviderSession:
        ...

    def next_action(self, request: ProviderRequest) -> ProviderResponse | Action:
        ...

    def interrupt(self, session: ProviderSession | None = None) -> None:
        ...

    def resume(self, session: ProviderSession, request: ProviderRequest) -> ProviderSession:
        ...

    def events(self, session: ProviderSession | None = None) -> Iterable[ProviderEvent]:
        ...

    def export_state(self) -> Mapping[str, Any]:
        ...

    def restore_state(self, value: Mapping[str, Any]) -> None:
        ...


class BaseProvider:
    """Convenience base class for deterministic and stub providers."""

    name = "provider"

    def __init__(self) -> None:
        self._session: ProviderSession | None = None
        self._interrupted = False

    def start(self, request: ProviderRequest) -> ProviderSession:
        session = ProviderSession(
            session_id="%s:%s" % (self.name, request.run_id),
            provider=self.name,
            metadata={"run_id": request.run_id},
        )
        self._session = session
        self._interrupted = False
        return session

    def resume(self, session: ProviderSession, request: ProviderRequest) -> ProviderSession:
        if session.provider != self.name:
            raise ProviderUnavailable("PROVIDER_SESSION_MISMATCH", "provider session belongs to another provider")
        self._session = session
        self._interrupted = False
        return session

    def interrupt(self, session: ProviderSession | None = None) -> None:
        self._interrupted = True

    def events(self, session: ProviderSession | None = None) -> Iterable[ProviderEvent]:
        return ()

    def export_state(self) -> Mapping[str, Any]:
        """Return provider-local progress safe to persist in a checkpoint."""
        return {}

    def restore_state(self, value: Mapping[str, Any]) -> None:
        if not isinstance(value, Mapping):
            raise ProviderUnavailable("CHECKPOINT_INVALID", "provider checkpoint state must be an object")

    def _check_interrupt(self) -> None:
        if self._interrupted:
            raise ProviderUnavailable("INTERRUPTED", "provider was interrupted")


__all__ = [
    "AgentProvider",
    "BaseProvider",
    "ProviderEvent",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderSession",
    "ProviderUnavailable",
]
