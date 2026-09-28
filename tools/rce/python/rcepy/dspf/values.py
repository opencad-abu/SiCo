"""SPICE numeric values and DSPF extension attributes."""

from __future__ import annotations

import math
import re
from typing import Any


_NUMBER = re.compile(
    r"^\s*([+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?)"
    r"([A-Za-z]*)\s*$"
)
_ATTRIBUTE = re.compile(
    r"(?<!\S)(?:\$?([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*"
    r"(\"[^\"]*\"|'[^']*'|[^\s]+)|"
    r"\$([A-Za-z_][A-Za-z0-9_.-]*)(?![A-Za-z0-9_.-]|\s*=))"
)
_MULTIPLIERS = {
    "": 1.0,
    "T": 1.0e12,
    "G": 1.0e9,
    "MEG": 1.0e6,
    "K": 1.0e3,
    "M": 1.0e-3,
    "U": 1.0e-6,
    "N": 1.0e-9,
    "P": 1.0e-12,
    "F": 1.0e-15,
}
_UNIT_TAILS = {"", "F", "OHM", "OHMS", "H", "V", "A", "S"}
_PLAIN_UNITS = {
    "FARAD", "FARADS", "OHM", "OHMS", "H", "HENRY", "HENRIES",
    "V", "VOLT", "VOLTS", "A", "AMP", "AMPS", "S",
}


def parse_spice_number(token: str) -> float:
    """Parse a finite SPICE value and normalize it to SI units."""
    raw = str(token)
    stripped = raw.strip()
    if stripped.isascii() and "_" not in stripped:
        try:
            value = float(stripped)
        except ValueError:
            pass
        else:
            if not math.isfinite(value):
                raise ValueError(f"Non-finite SPICE numeric value: {token!r}")
            return value
    match = _NUMBER.match(raw)
    if not match:
        raise ValueError(f"Invalid SPICE numeric value: {token!r}")
    number, tail = match.groups()
    upper = tail.upper()
    if not upper:
        suffix, unit = "", ""
    elif upper in _PLAIN_UNITS:
        suffix, unit = "", upper
    elif upper.startswith("MEG"):
        suffix, unit = "MEG", upper[3:]
    elif upper and upper[0] in _MULTIPLIERS:
        suffix, unit = upper[0], upper[1:]
    else:
        raise ValueError(f"Unsupported SPICE numeric suffix in {token!r}")
    if unit not in _UNIT_TAILS and suffix:
        raise ValueError(f"Unsupported trailing unit in {token!r}")
    value = float(number) * _MULTIPLIERS[suffix]
    if not math.isfinite(value):
        raise ValueError(f"Non-finite SPICE numeric value: {token!r}")
    return value


def optional_spice_number(value: Any) -> float | None:
    """Parse an optional attribute, returning None for absent/invalid values."""
    if value is None or value is True:
        return None
    try:
        return parse_spice_number(str(value))
    except ValueError:
        return None


def parse_attributes(text: str) -> dict[str, str | bool]:
    """Collect ``$key=value`` and ``key=value`` DSPF extension attributes."""
    if not text or ("=" not in text and "$" not in text):
        return {}
    result: dict[str, str | bool] = {}
    for match in _ATTRIBUTE.finditer(text):
        name, value, flag = match.groups()
        if name is None:
            result.setdefault(str(flag).lower(), True)
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[name.lower()] = value.rstrip(",;")
    return result


def layer_attribute(attributes: dict[str, str | bool]) -> str | None:
    """Return the best normalized layer reference available on a record."""
    for key in ("layer", "lvl"):
        value = attributes.get(key)
        if value not in (None, True):
            return str(value)
    first = attributes.get("layer1", attributes.get("lvl1"))
    second = attributes.get("layer2", attributes.get("lvl2"))
    if first not in (None, True) or second not in (None, True):
        return ":".join(str(item) for item in (first, second) if item not in (None, True))
    return None
