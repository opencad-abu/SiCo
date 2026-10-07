"""Immutable provider transport envelopes and their constructors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .protocol_constants import PROTOCOL_VERSION, ErrorCode, IDENTIFIER_PATTERN
from .protocol_errors import ProtocolError
from .protocol_validation import require_known_fields
from .value_codec import ensure_json, freeze, redact, thaw
from .protocol_errors import AgentError


@dataclass(frozen=True)
class Envelope:
    """A JSON-RPC-like envelope used for both messages and JSONL events."""

    message_type: str
    message_id: str
    run_id: str
    sequence: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    error: AgentError | None = None
    protocol_version: str = PROTOCOL_VERSION
    idempotency_key: str | None = None

    _FIELDS = frozenset(
        {
            "protocol_version",
            "message_type",
            "message_id",
            "run_id",
            "sequence",
            "payload",
            "error",
            "idempotency_key",
        }
    )

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "unsupported protocol version"
            )
        if self.message_type not in {"request", "response", "event"}:
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE,
                "message_type must be request, response, or event",
            )
        for name, value in (("message_id", self.message_id), ("run_id", self.run_id)):
            if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
                raise ProtocolError(
                    ErrorCode.INVALID_ENVELOPE, "%s is not a safe identifier" % name
                )
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "sequence must be a non-negative integer"
            )
        if not isinstance(self.payload, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "payload must be an object")
        ensure_json(self.payload)
        if self.error is not None and not isinstance(self.error, AgentError):
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "error must be AgentError or null"
            )
        if self.idempotency_key is not None and (
            not isinstance(self.idempotency_key, str)
            or not IDENTIFIER_PATTERN.fullmatch(self.idempotency_key)
        ):
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "idempotency_key is invalid"
            )
        # A response must carry either a payload or an error, but never both;
        # requests/events cannot carry an error object.  Keeping this rule in
        # the typed envelope prevents ambiguous JSON-RPC framing at the
        # provider and tool boundaries.
        if self.message_type == "response" and self.error is not None and self.payload:
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE,
                "response cannot contain both payload and error",
            )
        if self.message_type != "response" and self.error is not None:
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE,
                "only response envelopes may contain an error",
            )
        object.__setattr__(self, "payload", freeze(redact(self.payload)))

    @classmethod
    def from_dict(cls, value: object) -> "Envelope":
        if not isinstance(value, Mapping):
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "envelope must be an object"
            )
        require_known_fields(value, cls._FIELDS, "envelope")
        required = {
            "protocol_version",
            "message_type",
            "message_id",
            "run_id",
            "sequence",
            "payload",
        }
        missing = sorted(required - set(value))
        if missing:
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE,
                "envelope is missing required fields",
                {"fields": missing},
            )
        error = value.get("error")
        return cls(
            protocol_version=value.get("protocol_version"),
            message_type=value.get("message_type"),
            message_id=value.get("message_id"),
            run_id=value.get("run_id"),
            sequence=value.get("sequence"),
            payload=value.get("payload"),
            error=None if error is None else AgentError.from_dict(error),
            idempotency_key=value.get("idempotency_key"),
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "message_id": self.message_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "payload": thaw(self.payload),
        }
        if self.error is not None:
            value["error"] = self.error.to_dict()
        if self.idempotency_key is not None:
            value["idempotency_key"] = self.idempotency_key
        return value


def make_request(
    *,
    run_id: str,
    message_id: str,
    sequence: int,
    payload: Mapping[str, Any],
    idempotency_key: str | None = None,
) -> Envelope:
    return Envelope(
        "request",
        message_id,
        run_id,
        sequence,
        dict(payload),
        None,
        PROTOCOL_VERSION,
        idempotency_key,
    )


def make_response(
    *,
    run_id: str,
    message_id: str,
    sequence: int,
    payload: Mapping[str, Any],
    error: AgentError | None = None,
) -> Envelope:
    return Envelope("response", message_id, run_id, sequence, dict(payload), error)
