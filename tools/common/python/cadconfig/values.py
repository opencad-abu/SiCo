"""Immutable configuration values and detached input projections."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from types import MappingProxyType
from typing import Any


MISSING = object()


def freeze(value: Any) -> Any:
    """Copy TOML containers recursively, retaining no mutable caller reference."""
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Configuration table keys must be strings")
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if value is None or type(value) in (str, bool, int, float, date, datetime, time):
        return value
    raise ValueError(f"Unsupported configuration value: {type(value).__name__}")


def thaw(value: Any) -> Any:
    """Return a detached TOML-shaped projection for serialization or a new input."""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


def lookup(table: Mapping[str, Any], path: str, default: Any = MISSING) -> Any:
    value: Any = table
    for key in path.split("."):
        if not isinstance(value, Mapping) or key not in value:
            return default
        value = value[key]
    return value
