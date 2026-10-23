"""Machine-readable contract errors and immutable error payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .json_values import ensure_json, freeze, thaw
from .redaction import redact, redact_text
from .validation import require_known_fields


class ErrorCode(str, Enum):
    INVALID_ENVELOPE = "INVALID_ENVELOPE"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    INVALID_ACTION = "INVALID_ACTION"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    DUPLICATE_ACTION = "DUPLICATE_ACTION"
    STALE_SOURCE_GENERATION = "STALE_SOURCE_GENERATION"
    TEMPLATE_LOCK_MISMATCH = "TEMPLATE_LOCK_MISMATCH"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_SCHEMA_INVALID = "TOOL_SCHEMA_INVALID"
    PATH_DENIED = "PATH_DENIED"
    PATH_ESCAPE = "PATH_ESCAPE"
    SYMLINK_ESCAPE = "SYMLINK_ESCAPE"
    ENVIRONMENT_DENIED = "ENVIRONMENT_DENIED"
    CONTEXT_LIMIT_EXCEEDED = "CONTEXT_LIMIT_EXCEEDED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    MODEL_PROVIDER_UNAVAILABLE = "MODEL_PROVIDER_UNAVAILABLE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_DISCONNECTED = "PROVIDER_DISCONNECTED"
    UNKNOWN_PROVIDER_STATE = "UNKNOWN_PROVIDER_STATE"
    UNKNOWN_SIDE_EFFECT = "UNKNOWN_SIDE_EFFECT"
    INTERRUPTED = "INTERRUPTED"
    INVALID_STATE = "INVALID_STATE"
    CHECKPOINT_INVALID = "CHECKPOINT_INVALID"
    BUNDLE_INVALID = "BUNDLE_INVALID"
    BUNDLE_HASH_MISMATCH = "BUNDLE_HASH_MISMATCH"
    BUNDLE_PROTOCOL_MISMATCH = "BUNDLE_PROTOCOL_MISMATCH"
    BUNDLE_SOURCE_MISMATCH = "BUNDLE_SOURCE_MISMATCH"
    BUNDLE_TEMPLATE_MISMATCH = "BUNDLE_TEMPLATE_MISMATCH"
    DETERMINISTIC_GATE_REQUIRED = "DETERMINISTIC_GATE_REQUIRED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ProtocolError(ValueError):
    """A protocol rejection with a stable machine-readable error code."""

    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ):
        self.code = str(code.value if isinstance(code, ErrorCode) else code)
        # Protocol errors are themselves observable data.  Normalize their
        # message/details at construction so an exception raised by a hostile
        # adapter cannot retain a credential or a cyclic Python object while
        # it is being converted into an event/checkpoint error.
        self.message = redact_text(str(message))
        raw_details = details if isinstance(details, Mapping) else {}
        self.details = freeze(redact(raw_details))
        try:
            ensure_json(self.details)
        except ProtocolError:
            # Error reporting must remain total even when the original detail
            # object is malformed.  The stable code/message still carry the
            # useful boundary information without copying unsafe data.
            self.details = freeze({})
        super().__init__(self.message)

    def to_error(self) -> "AgentError":
        return AgentError(self.code, self.message, self.details)


@dataclass(frozen=True)
class AgentError:
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code or len(self.code) > 128:
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "error.code must be non-empty text"
            )
        if not isinstance(self.message, str) or not self.message:
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "error.message must be non-empty text"
            )
        if not isinstance(self.details, Mapping):
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "error.details must be an object"
            )
        # Error instances are commonly constructed directly from provider or
        # OS exceptions, before they reach an event/checkpoint serializer.
        # Normalize them at construction so the in-memory result cannot expose
        # a credential merely because a caller inspected ``last_error``.
        safe_message = redact_text(self.message)
        safe_details = freeze(redact(self.details))
        ensure_json(safe_details)
        object.__setattr__(self, "message", safe_message)
        object.__setattr__(self, "details", safe_details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": thaw(self.details),
        }

    @classmethod
    def from_dict(cls, value: object) -> "AgentError":
        if not isinstance(value, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "error must be an object")
        require_known_fields(value, {"code", "message", "details"}, "error")
        code = value.get("code")
        message = value.get("message")
        details = value.get("details", {})
        if not isinstance(code, str) or not code:
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "error.code must be non-empty text"
            )
        if not isinstance(message, str) or not message:
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "error.message must be non-empty text"
            )
        if not isinstance(details, Mapping):
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "error.details must be an object"
            )
        ensure_json(details)
        return cls(code, message, dict(details))
