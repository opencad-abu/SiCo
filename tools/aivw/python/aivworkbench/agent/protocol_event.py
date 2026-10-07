"""Immutable runtime event records and their constructor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .protocol_constants import PROTOCOL_VERSION, ErrorCode, IDENTIFIER_PATTERN
from .protocol_errors import ProtocolError
from .protocol_validation import require_known_fields
from .value_codec import ensure_json, freeze, redact, thaw
from .protocol_constants import EventType


@dataclass(frozen=True)
class Event:
    """Runtime event with monotonic sequence and a stable event id."""

    event_id: str
    event_type: str
    run_id: str
    sequence: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    turn_id: str | None = None
    action_id: str | None = None
    idempotency_key: str | None = None
    protocol_version: str = PROTOCOL_VERSION

    _FIELDS = frozenset(
        {
            "protocol_version",
            "event_id",
            "event_type",
            "run_id",
            "sequence",
            "payload",
            "turn_id",
            "action_id",
            "idempotency_key",
        }
    )

    def __post_init__(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "unsupported protocol version"
            )
        if self.event_type not in {item.value for item in EventType}:
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE,
                "unknown event type",
                {"event_type": self.event_type},
            )
        for name, value in (("event_id", self.event_id), ("run_id", self.run_id)):
            if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
                raise ProtocolError(
                    ErrorCode.INVALID_ENVELOPE, "%s is not a safe identifier" % name
                )
        if self.turn_id is not None and (
            not isinstance(self.turn_id, str)
            or not IDENTIFIER_PATTERN.fullmatch(self.turn_id)
        ):
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "turn_id is invalid")
        if self.action_id is not None and (
            not isinstance(self.action_id, str)
            or not IDENTIFIER_PATTERN.fullmatch(self.action_id)
        ):
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "action_id is invalid")
        if self.idempotency_key is not None and (
            not isinstance(self.idempotency_key, str)
            or not IDENTIFIER_PATTERN.fullmatch(self.idempotency_key)
        ):
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "idempotency_key is invalid"
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
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "event.payload must be an object"
            )
        ensure_json(self.payload)
        object.__setattr__(self, "payload", freeze(redact(self.payload)))

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "protocol_version": self.protocol_version,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "payload": thaw(self.payload),
        }
        if self.turn_id is not None:
            value["turn_id"] = self.turn_id
        if self.action_id is not None:
            value["action_id"] = self.action_id
        if self.idempotency_key is not None:
            value["idempotency_key"] = self.idempotency_key
        return value

    @classmethod
    def from_dict(cls, value: object) -> "Event":
        if not isinstance(value, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "event must be an object")
        require_known_fields(value, cls._FIELDS, "event")
        required = {
            "protocol_version",
            "event_id",
            "event_type",
            "run_id",
            "sequence",
            "payload",
        }
        missing = sorted(required - set(value))
        if missing:
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE,
                "event is missing required fields",
                {"fields": missing},
            )
        return cls(
            protocol_version=value.get("protocol_version"),
            event_id=value.get("event_id"),
            event_type=value.get("event_type"),
            run_id=value.get("run_id"),
            sequence=value.get("sequence"),
            payload=value.get("payload"),
            turn_id=value.get("turn_id"),
            action_id=value.get("action_id"),
            idempotency_key=value.get("idempotency_key"),
        )


def make_event(
    event_type: EventType | str,
    *,
    run_id: str,
    sequence: int,
    payload: Mapping[str, Any] | None = None,
    turn_id: str | None = None,
    action_id: str | None = None,
    idempotency_key: str | None = None,
    event_id: str | None = None,
) -> Event:
    identifier = event_id or "evt-%08d" % sequence
    kind = event_type.value if isinstance(event_type, EventType) else str(event_type)
    return Event(
        identifier,
        kind,
        run_id,
        sequence,
        dict(payload or {}),
        turn_id,
        action_id,
        idempotency_key,
    )
