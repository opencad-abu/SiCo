"""Immutable provider proposals with validated budget and candidate parameters."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from typing import Any, Mapping

from .protocol_constants import (
    ACTION_KINDS,
    ErrorCode,
    IDENTIFIER_PATTERN,
    GENERATION_PATTERN,
    MAX_BUDGET_VALUE,
)
from .protocol_errors import ProtocolError
from .protocol_validation import require_known_fields
from .value_codec import ensure_json, freeze, redact, reject_verdict_fields, thaw


@dataclass(frozen=True)
class Action:
    """One provider proposal.

    ``Action`` is deliberately not a verdict type.  A provider may explain a
    proposed candidate, but deterministic gates remain outside this schema.
    """

    action_id: str
    kind: str
    parent_event_id: str
    expected_source_generation: str
    budget: Mapping[str, Any] = field(default_factory=dict)
    params: Mapping[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    template_lock: str | None = None

    _FIELDS = frozenset(
        {
            "action_id",
            "kind",
            "parent_event_id",
            "expected_source_generation",
            "budget",
            "params",
            "idempotency_key",
            "template_lock",
        }
    )
    _REQUIRED = frozenset(
        {
            "action_id",
            "kind",
            "parent_event_id",
            "expected_source_generation",
            "budget",
            "params",
        }
    )

    def __post_init__(self) -> None:
        self._validate()
        # Keep the immutable action boundary JSON-safe and redacted even when
        # callers construct it directly instead of using ``from_dict``.
        object.__setattr__(self, "budget", freeze(redact(self.budget)))
        object.__setattr__(self, "params", freeze(redact(self.params)))

    @classmethod
    def from_dict(cls, value: object) -> "Action":
        if not isinstance(value, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ACTION, "action must be an object")
        require_known_fields(value, cls._FIELDS, "action")
        missing = sorted(cls._REQUIRED - set(value))
        if missing:
            raise ProtocolError(
                ErrorCode.INVALID_ACTION,
                "action is missing required fields",
                {"fields": missing},
            )
        return cls(
            action_id=value.get("action_id"),
            kind=value.get("kind"),
            parent_event_id=value.get("parent_event_id"),
            expected_source_generation=value.get("expected_source_generation"),
            budget=value.get("budget"),
            params=value.get("params"),
            idempotency_key=value.get("idempotency_key"),
            template_lock=value.get("template_lock"),
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "action_id": self.action_id,
            "kind": self.kind,
            "parent_event_id": self.parent_event_id,
            "expected_source_generation": self.expected_source_generation,
            "budget": thaw(self.budget),
            "params": thaw(self.params),
        }
        if self.idempotency_key is not None:
            value["idempotency_key"] = self.idempotency_key
        if self.template_lock is not None:
            value["template_lock"] = self.template_lock
        return value

    @property
    def idempotency(self) -> str:
        return self.idempotency_key or self.action_id

    def digest(self) -> str:
        import hashlib

        encoded = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _validate(self) -> None:
        if not isinstance(self.action_id, str) or not IDENTIFIER_PATTERN.fullmatch(
            self.action_id
        ):
            raise ProtocolError(
                ErrorCode.INVALID_ACTION, "action_id is not a safe identifier"
            )
        if not isinstance(self.kind, str) or self.kind not in ACTION_KINDS:
            raise ProtocolError(
                ErrorCode.INVALID_ACTION, "unknown action kind", {"kind": self.kind}
            )
        if not isinstance(
            self.parent_event_id, str
        ) or not IDENTIFIER_PATTERN.fullmatch(self.parent_event_id):
            raise ProtocolError(
                ErrorCode.INVALID_ACTION, "parent_event_id is not a safe identifier"
            )
        if not isinstance(
            self.expected_source_generation, str
        ) or not GENERATION_PATTERN.fullmatch(self.expected_source_generation):
            raise ProtocolError(
                ErrorCode.INVALID_ACTION, "expected_source_generation is invalid"
            )
        if self.idempotency_key is not None and (
            not isinstance(self.idempotency_key, str)
            or not IDENTIFIER_PATTERN.fullmatch(self.idempotency_key)
        ):
            raise ProtocolError(ErrorCode.INVALID_ACTION, "idempotency_key is invalid")
        if self.template_lock is not None and (
            not isinstance(self.template_lock, str)
            or not GENERATION_PATTERN.fullmatch(self.template_lock)
        ):
            raise ProtocolError(ErrorCode.INVALID_ACTION, "template_lock is invalid")
        if not isinstance(self.budget, Mapping):
            raise ProtocolError(
                ErrorCode.INVALID_ACTION, "action.budget must be an object"
            )
        require_known_fields(
            self.budget,
            {
                "tokens",
                "tool_calls",
                "wall_seconds",
                "context_bytes",
                "turns",
                "simulation_cases",
                "simulation_seconds",
            },
            "action.budget",
        )
        for name, raw in self.budget.items():
            if (
                not isinstance(raw, (int, float))
                or isinstance(raw, bool)
                or not math.isfinite(float(raw))
            ):
                raise ProtocolError(
                    ErrorCode.INVALID_ACTION,
                    "budget values must be finite numbers",
                    {"field": name},
                )
            if float(raw) < 0 or float(raw) > MAX_BUDGET_VALUE:
                raise ProtocolError(
                    ErrorCode.INVALID_ACTION,
                    "budget value is outside the supported range",
                    {"field": name},
                )
        if not isinstance(self.params, Mapping):
            raise ProtocolError(
                ErrorCode.INVALID_ACTION, "action.params must be an object"
            )
        ensure_json(self.params)
        reject_verdict_fields(self.params)
