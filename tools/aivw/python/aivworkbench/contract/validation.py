"""Shared rejection of unknown contract object fields."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def require_known_fields(
    value: Mapping[str, Any], allowed: Iterable[str], label: str
) -> None:
    # Lazy import keeps errors -> validation one-way during module load.
    from .errors import ErrorCode, ProtocolError

    allowed_set = set(allowed)
    # JSON object names must be text.  Check that explicitly before sorting so
    # a hostile mapping such as ``{1: "x"}`` yields a protocol error rather
    # than a Python ``TypeError`` while comparing heterogeneous keys.
    unknown = [
        key for key in value if not isinstance(key, str) or key not in allowed_set
    ]
    if unknown:
        fields = sorted(str(item) for item in unknown)
        raise ProtocolError(
            ErrorCode.UNKNOWN_FIELD,
            "%s contains unknown field(s)" % label,
            {"fields": fields},
        )
