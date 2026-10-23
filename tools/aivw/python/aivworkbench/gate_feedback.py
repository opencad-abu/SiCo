"""Deterministic checker feedback attached to a candidate revision.

Core owner for the feedback record exchanged by behavior gates and calibration
audits.  The retiring agent qualification models re-export this class until the
agent retirement batch deletes them (see docs/AGENT_RETIREMENT_PLAN.md).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from .contract.errors import ErrorCode, ProtocolError
from .contract.json_values import ensure_json, freeze, thaw
from .contract.redaction import redact as redact_secrets


_FORBIDDEN_VERDICT_KEYS = frozenset(
    {"verdict", "gate_verdict", "deterministic_verdict", "ai_verdict", "pass_fail"}
)


_MAX_EVIDENCE_BYTES = 256 * 1024


def _canonical(value: object) -> str:
    """Return a strict, deterministic JSON representation."""

    safe = redact_secrets(value)
    ensure_json(safe)
    return json.dumps(
        safe,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _bounded(value: object, *, cap: int = _MAX_EVIDENCE_BYTES) -> Any:
    """Redact and bound an observable payload without retaining aliases."""

    safe = redact_secrets(value)
    ensure_json(safe)
    encoded = json.dumps(
        safe,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) <= cap:
        return thaw(freeze(safe))
    # A digest and a fixed marker preserve auditability while preventing a
    # provider response from expanding a qualification record without bound.
    return {
        "_truncated": True,
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "bytes": len(encoded.encode("utf-8")),
    }


def _assert_no_forbidden_verdict(value: object, path: str = "value", seen: set[int] | None = None) -> None:
    """Reject provider-owned verdict fields at the qualification boundary."""

    active = set() if seen is None else seen
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification payload contains a cyclic object", {"path": path})
        active.add(identity)
        try:
            for key, child in value.items():
                if str(key).lower() in _FORBIDDEN_VERDICT_KEYS:
                    raise ProtocolError(
                        ErrorCode.INVALID_ARGUMENTS,
                        "provider payload contains a verdict field",
                        {"path": path + "." + str(key)},
                    )
                _assert_no_forbidden_verdict(child, path + "." + str(key), active)
        finally:
            active.remove(identity)
    elif isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification payload contains a cyclic object", {"path": path})
        active.add(identity)
        try:
            for index, child in enumerate(value):
                _assert_no_forbidden_verdict(child, "%s[%d]" % (path, index), active)
        finally:
            active.remove(identity)


@dataclass(frozen=True)
class GateFeedback:
    """Deterministic checker feedback attached to a candidate revision."""

    status: str
    code: str
    summary: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if self.status not in {"PASS", "FAIL", "BLOCKED"}:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "gate feedback status is invalid")
        if not isinstance(self.code, str) or not self.code:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "gate feedback code is invalid")
        if not isinstance(self.summary, Mapping) or not isinstance(self.evidence, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "gate feedback payload must be objects")
        _assert_no_forbidden_verdict(self.summary, "summary")
        _assert_no_forbidden_verdict(self.evidence, "evidence")
        summary = freeze(_bounded(self.summary))
        evidence = freeze(_bounded(self.evidence))
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "sha256", _digest({"status": self.status, "code": self.code, "summary": thaw(summary), "evidence": thaw(evidence)}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "code": self.code,
            "summary": thaw(self.summary),
            "evidence": thaw(self.evidence),
            "sha256": self.sha256,
        }
