"""Live-model wire values and bounded JSON validation primitives."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

LIVE_PROTOCOL_VERSION = "aivw-live-model-v1"


MAX_LIVE_ID_LENGTH = 160


MAX_STATUS_BYTES = 48 * 1024


MAX_EVENT_HISTORY = 128


DEFAULT_DEBOUNCE_MS = 750


MAX_DEBOUNCE_MS = 10_000


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,159}$")


_GENERATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,255}$")


_VIEW_KIND = frozenset({"schematic", "symbol", "config", "spec", "output_view", "layout"})


_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|authorization|password|passwd|secret|private[_-]?key|session[_-]?token|credential|token)",
    re.IGNORECASE,
)


_VERDICT_KEY = frozenset(
    {"verdict", "provider_verdict", "provider_status", "provider_decision", "decision"}
)


class LiveModelProtocolError(ValueError):
    """A fail-closed live-model protocol rejection."""

    def __init__(self, code: str, message: str):
        self.code = str(code)
        self.message = str(message)
        super().__init__(self.message)


def _reject_constant(value: str) -> Any:
    raise ValueError("non-finite JSON number: %s" % value)


def loads_strict(raw: bytes | str) -> dict[str, Any]:
    """Decode one strict JSON object, rejecting duplicate keys and NaN."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON object key: %s" % key)
            result[key] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=_reject_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LiveModelProtocolError("invalid_json", str(exc)) from exc
    if not isinstance(value, dict):
        raise LiveModelProtocolError("invalid_message", "live-model message must be an object")
    return value


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LiveModelProtocolError("invalid_number", "%s must be a finite number" % label)
    converted = float(value)
    if not math.isfinite(converted):
        raise LiveModelProtocolError("invalid_number", "%s must be a finite number" % label)
    return converted


def _identifier(value: Any, label: str, *, generation: bool = False) -> str:
    pattern = _GENERATION if generation else _IDENTIFIER
    if not isinstance(value, str) or not value or pattern.fullmatch(value) is None:
        raise LiveModelProtocolError("invalid_identifier", "%s is invalid" % label)
    return value


def _copy_checked(value: Any, *, key: str = "") -> Any:
    """Copy JSON-shaped data while rejecting credentials and verdict claims."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise LiveModelProtocolError("invalid_payload", "object keys must be text")
            lowered = raw_key.casefold()
            if _SECRET_KEY.search(raw_key) and lowered not in {"source_generation"}:
                raise LiveModelProtocolError("credential_forbidden", "credentials are not allowed")
            if lowered in _VERDICT_KEY:
                raise LiveModelProtocolError("verdict_forbidden", "provider verdict fields are not allowed")
            result[raw_key] = _copy_checked(child, key=raw_key)
        return result
    if isinstance(value, (list, tuple)):
        return [_copy_checked(child, key=key) for child in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise LiveModelProtocolError("invalid_number", "payload contains a non-finite number")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise LiveModelProtocolError("invalid_payload", "payload contains a non-JSON value")


def _object(value: Any, label: str, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LiveModelProtocolError("invalid_payload", "%s must be an object" % label)
    if any(not isinstance(item, str) for item in value):
        raise LiveModelProtocolError("invalid_payload", "%s object keys must be text" % label)
    keys = set(value)
    unknown = keys - allowed
    missing = required - keys
    if unknown:
        raise LiveModelProtocolError("unknown_field", "%s has unknown field(s): %s" % (label, sorted(unknown)))
    if missing:
        raise LiveModelProtocolError("missing_field", "%s is missing field(s): %s" % (label, sorted(missing)))
    return _copy_checked(dict(value), key=label)


def _validate_target(value: Any, label: str) -> dict[str, Any]:
    target = _object(value, label, {"library", "cell", "module"}, {"library", "cell", "module"})
    for key in ("library", "cell", "module"):
        _identifier(target[key], "%s.%s" % (label, key))
    return target


def _validate_view(value: Any, label: str) -> dict[str, Any]:
    view = _object(
        value,
        label,
        {"library", "cell", "view", "view_type", "kind"},
        {"library", "cell", "view", "view_type", "kind"},
    )
    for key in ("library", "cell", "view", "view_type"):
        _identifier(view[key], "%s.%s" % (label, key))
    kind = view["kind"]
    if not isinstance(kind, str) or kind not in _VIEW_KIND:
        raise LiveModelProtocolError("invalid_view", "%s.kind is unsupported" % label)
    return view


def _validate_target_view_binding(target: Mapping[str, Any], view: Mapping[str, Any]) -> None:
    """Require a live event to identify the same cell and a schematic view."""

    if target["library"] != view["library"] or target["cell"] != view["cell"]:
        raise LiveModelProtocolError(
            "identity_mismatch", "target and view_identity must name the same cell"
        )
    if view["kind"] != "schematic":
        raise LiveModelProtocolError(
            "invalid_view", "live-model source events require a schematic view"
        )


def _bounded_sequence(value: Any, label: str, *, allow_zero: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LiveModelProtocolError("invalid_sequence", "%s must be a bounded integer" % label)
    minimum = 0 if allow_zero else 1
    if not minimum <= value <= 2**63 - 1:
        raise LiveModelProtocolError("invalid_sequence", "%s is outside the bounded range" % label)
    return value


def _require_bounded_text(
    value: Any,
    label: str,
    limit: int,
    *,
    nonempty: bool = False,
    ascii_only: bool = False,
) -> str:
    """Validate text length instead of silently truncating protocol fields."""
    if not isinstance(value, str):
        raise LiveModelProtocolError("invalid_payload", "%s must be text" % label)
    if nonempty and not value:
        raise LiveModelProtocolError("invalid_payload", "%s must not be empty" % label)
    if len(value) > limit:
        raise LiveModelProtocolError("payload_too_large", "%s exceeds %d bytes" % (label, limit))
    if ascii_only and any(ord(character) < 0x20 or ord(character) > 0x7E for character in value):
        raise LiveModelProtocolError("invalid_payload", "%s contains unsupported control text" % label)
    return value
