"""Credential detection and recursive redaction for agent values."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|auth(?:orization)?|password|passwd|secret|private[_-]?key|session[_-]?token|token|credential)",
    re.IGNORECASE,
)


_SECRET_VALUE = re.compile(
    r"(?i)(bearer\s+|(?:api[_-]?key|access[_-]?token|password|secret|token|credential|authorization)\s*[=:]\s*)([^\s,;]+)"
)


_SECRET_TOKEN = re.compile(
    r"\b(?:sk|key|tok|pat|ghp|glpat)[_-][A-Za-z0-9_-]{12,}\b|\bsk-[A-Za-z0-9_-]{12,}\b",
    re.IGNORECASE,
)


_NON_SECRET_ACCOUNTING_KEYS = frozenset(
    {
        "tokens",
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "tokens_used",
        "tokens_remaining",
        "token_budget",
        "token_limit",
    }
)


_REFERENCE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _is_secret_key(key: str, value: Any = None) -> bool:
    if key.lower() in {"secret_reference", "secret_ref"} and isinstance(value, Mapping):
        if (
            set(value) <= {"source", "name"}
            and value.get("source") == "environment"
            and isinstance(value.get("name"), str)
            and _REFERENCE_NAME.fullmatch(value["name"]) is not None
        ):
            return False
    if key.lower() in _NON_SECRET_ACCOUNTING_KEYS:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return not math.isfinite(float(value))
        return True
    return _SECRET_KEY.search(key) is not None


def redact(value: object, _seen: set[int] | None = None) -> Any:
    """Copy a value while removing credentials and unsupported objects."""

    if _seen is None:
        _seen = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in _seen:
            return "[REDACTED_CYCLE]"
        _seen.add(identity)
        result: dict[str, Any] = {}
        try:
            for raw_key, raw_value in value.items():
                key = str(raw_key)
                result[key] = "[REDACTED]" if _is_secret_key(key, raw_value) else redact(raw_value, _seen)
        finally:
            _seen.remove(identity)
        return result
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in _seen:
            return "[REDACTED_CYCLE]"
        _seen.add(identity)
        try:
            return [redact(item, _seen) for item in value]
        finally:
            _seen.remove(identity)
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[REDACTED_UNSUPPORTED]"


def redact_text(value: str) -> str:
    redacted = _SECRET_VALUE.sub(lambda match: match.group(1) + "[REDACTED]", value)
    return _SECRET_TOKEN.sub("[REDACTED]", redacted)


def contains_secret_name(value: str) -> bool:
    """Identify credential-bearing locator names without exposing regexes."""
    return _SECRET_KEY.search(value) is not None
