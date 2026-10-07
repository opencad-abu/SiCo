"""Finite qualification evidence values and digest contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")


_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/+@=-]{0,255}$")


_FORBIDDEN_VERDICT_KEYS = frozenset(
    {"verdict", "gate_verdict", "deterministic_verdict", "ai_verdict", "pass_fail"}
)


def qualification_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def qualification_canonical(value: object) -> str:
    """Serialize a validated value without aliases or non-finite numbers."""

    safe = qualification_copy_json(value)
    return json.dumps(
        safe,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def qualification_digest(value: object) -> str:
    return hashlib.sha256(qualification_canonical(value).encode("utf-8")).hexdigest()


def qualification_copy_json(value: object, seen: set[int] | None = None) -> Any:
    """Make a plain, finite JSON-shaped copy and reject cycles."""

    active = set() if seen is None else seen
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite number is not allowed")
        return float(value)
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic mapping is not allowed")
        active.add(identity)
        try:
            result: dict[str, Any] = {}
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ValueError("JSON object keys must be text")
                result[key] = qualification_copy_json(child, active)
            return result
        finally:
            active.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic sequence is not allowed")
        active.add(identity)
        try:
            return [qualification_copy_json(child, active) for child in value]
        finally:
            active.remove(identity)
    raise ValueError("value is not JSON-compatible")


def qualification_reject_verdict_fields(value: object, path: str = "value", seen: set[int] | None = None) -> None:
    active = set() if seen is None else seen
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic provider evidence at %s" % path)
        active.add(identity)
        try:
            for key, child in value.items():
                if str(key).lower() in _FORBIDDEN_VERDICT_KEYS:
                    raise ValueError("provider-owned verdict field at %s.%s" % (path, key))
                qualification_reject_verdict_fields(child, "%s.%s" % (path, key), active)
        finally:
            active.remove(identity)
    elif isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic provider evidence at %s" % path)
        active.add(identity)
        try:
            for index, child in enumerate(value):
                qualification_reject_verdict_fields(child, "%s[%d]" % (path, index), active)
        finally:
            active.remove(identity)


def qualification_valid_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _require_digest(value: object, label: str) -> str:
    if not qualification_valid_digest(value):
        raise ValueError("%s must be a sha256 digest" % label)
    return str(value)


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError("%s is invalid" % label)
    return value


def _safe_digest_field(value: object, label: str, findings: list[str], *, required: bool = True) -> None:
    """Validate a digest field without echoing an untrusted value."""

    if value is None and not required:
        return
    if not qualification_valid_digest(value):
        findings.append("%s is missing or malformed" % label)


# Read-only evidence authority values. cdns-ipc is the current component name;
# the virtuoso-ipc value is retained so records written before the 2026-09-21
# rename still validate.
READ_ONLY_AUTHORITIES = frozenset({
    "authenticated-cdns-ipc-read-only",
    "authenticated-virtuoso-ipc-read-only",
})
