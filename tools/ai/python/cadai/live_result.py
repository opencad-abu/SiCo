"""Bound and redact AIVW runner metadata before it enters live status."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .live_protocol import _VERDICT_KEY, LiveModelProtocolError, _copy_checked

_SECRET_VALUE = re.compile(
    r"(?i)(bearer\s+|(?:api[_-]?key|access[_-]?token|authorization|password|passwd|secret|private[_-]?key|session[_-]?token|credential|token)\s*[=:]\s*)([^\s,;]+)"
)


_OPAQUE_SECRET = re.compile(
    r"\b(?:sk|key|tok|pat|ghp|glpat)[_-][A-Za-z0-9_-]{12,}\b",
    re.IGNORECASE,
)


def _bounded_text(value: Any, limit: int = 512) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    return value[:limit]


def _sanitize_error_text(value: Any, limit: int = 512) -> str:
    """Return bounded diagnostics with credential-looking fragments removed."""
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        text = type(value).__name__
    # Keep diagnostics useful while ensuring common key/value and bearer forms
    # cannot cross the status/history/event-sink boundary.
    text = re.sub(
        r"(?i)(bearer\s+)[^\s,;]+",
        r"\1<redacted>",
        text,
    )
    text = re.sub(
        r"(?i)((?:api[_-]?key|access[_-]?token|authorization|password|passwd|secret|private[_-]?key|session[_-]?token|credential|token)\s*[=:]\s*)[^\s,;]+",
        r"\1<redacted>",
        text,
    )
    text = _SECRET_VALUE.sub(lambda match: match.group(1) + "<redacted>", text)
    text = _OPAQUE_SECRET.sub("<redacted>", text)
    return text[:limit]


def _bounded_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only non-sensitive, bounded result metadata from AIVW."""

    # A provider must never be able to assert a design verdict through a
    # provider-owned field.  The trusted AIVW workflow status is carried in
    # ``status`` below; provider verdict/decision claims are a protocol error.
    if any(key.casefold() in _VERDICT_KEY for key in value if isinstance(key, str)):
        raise LiveModelProtocolError("verdict_forbidden", "provider verdict fields are not allowed")

    result: dict[str, Any] = {}
    status = value.get("status")
    if isinstance(status, str):
        result["result_status"] = _sanitize_error_text(status, 96)
    # ``source_generation`` is retained for the legacy runner contract.  The
    # live controller additionally distinguishes the optimistic save token
    # (``requested_source_generation``) from the authoritative OA snapshot
    # hash (``snapshot_source_generation``/``validated_source_generation``).
    for key in (
        "run_id",
        "source_generation",
        "requested_source_generation",
        "snapshot_source_generation",
        "validated_source_generation",
    ):
        text = _bounded_text(value.get(key), 256)
        if text is not None:
            result[key] = _sanitize_error_text(text, 256)
    artifacts = value.get("artifacts")
    if isinstance(artifacts, (list, tuple)):
        bounded: list[str] = []
        for item in artifacts[:32]:
            if isinstance(item, str):
                bounded.append(_sanitize_error_text(item, 512))
        result["artifacts"] = bounded
    code = _bounded_text(value.get("code"), 128)
    if code:
        result["code"] = _sanitize_error_text(code, 128)
    message = _bounded_text(value.get("message"), 512)
    if message:
        result["message"] = _sanitize_error_text(message, 512)
    # Ensure a hostile runner cannot smuggle a credential through a nested
    # result object.  This also rejects non-finite values before serialization.
    checked = _copy_checked(result)
    encoded = json.dumps(checked, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > 8 * 1024:
        raise LiveModelProtocolError("result_too_large", "live-model result metadata is too large")
    return checked
