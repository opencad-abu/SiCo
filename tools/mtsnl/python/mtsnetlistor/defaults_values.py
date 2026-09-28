"""JSON-compatible defaults values and safe text validation."""

from __future__ import annotations

from typing import Any, Mapping
from .errors import RequestValidationError


DEFAULTS_SCHEMA_VERSION = 1


DEFAULTS_OUTPUT_ENV = "MTS_NETLISTOR_DEFAULTS_OUTPUT"


DEFAULTS_PROJECT_ENV = "MTS_NETLISTOR_PROBE_PROJECT"


DEFAULTS_RESULTS_ENV = "MTS_NETLISTOR_PROBE_RESULTS"


DEFAULTS_PROVIDERS = frozenset({"asi_initialization", "mae_test"})


def _normalise(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    return str(value)


def _safe_text(value: Any, label: str) -> str:
    text = str(value)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise RequestValidationError(f"{label} contains a control character")
    return text


def _normalize_named_options(value: Any) -> dict[str, Any]:
    """Convert SKILL association-list JSON arrays to a Python mapping.

    The probe's SKILL serializer represents ``(("temp" 27) ("scale" 1))``
    as ``[["temp", 27], ["scale", 1]]``.  Simulator entries may themselves
    be association lists, for example ``["reltol", [["value", 0.001]]]``.
    Keeping this conversion in one place prevents process values and option
    choices from being dropped when a PDK returns list-shaped data.
    """

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(item, (list, tuple)) and item and all(
                isinstance(pair, (list, tuple)) and len(pair) >= 2 for pair in item
            ):
                item = {str(pair[0]): _normalise(pair[1]) for pair in item}
            result[str(key)] = _normalise(item)
        return result
    if not isinstance(value, (list, tuple)):
        return {}
    result: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        name = str(item[0])
        raw = item[1]
        if isinstance(raw, (list, tuple)) and raw and all(
            isinstance(pair, (list, tuple)) and len(pair) >= 2 for pair in raw
        ):
            raw = {str(pair[0]): _normalise(pair[1]) for pair in raw}
        result[name] = _normalise(raw)
    return result
