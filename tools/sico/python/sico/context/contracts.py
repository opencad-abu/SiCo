"""Small shared helpers for context contract construction."""

from __future__ import annotations

import hashlib
import json

PAYLOAD_SCHEMA = "sico.context.payload.v1"
ENVELOPE_SCHEMA = "sico.context.envelope.v1"
MANIFEST_SCHEMA = "sico.context.manifest.v1"

TURN_STEPS = frozenset({
    "session_start", "route", "read_plan", "read_result", "write_plan",
    "confirm", "execute", "verify", "follow_up", "compact",
})

TARGET_STATES = frozenset({"cellview", "ade_session", "invalid_view", "unavailable"})


def string(value, name, *, allow_empty=True):
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"{name} must be a string")
    return value


def canonical_json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def canonical_bytes(value) -> bytes:
    return canonical_json(value).encode("utf-8")


def sha256_json(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()
