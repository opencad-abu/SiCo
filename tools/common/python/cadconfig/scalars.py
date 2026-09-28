"""Shared scalar validation independent of profile and execution file layouts."""

from __future__ import annotations

from typing import Any, Mapping

from .paths import PATH_KEYS
from .values import MISSING, lookup


INTEGER_TEXT_PATHS = frozenset({
    "run.cpus", "runtime.cpus", "runtime.lvs_cpus", "runtime.ext_cpus",
    "batch.parallel_cells",
})


def positive_integer_text(value: Any, path: str) -> str:
    if type(value) not in (str, int):
        raise ValueError(f"{path} must be a positive integer")
    text = str(value).strip()
    if not text.isascii() or not text.isdigit() or int(text) < 1:
        raise ValueError(f"{path} must be a positive integer")
    return str(int(text))


def validate_scalars(raw: Mapping[str, Any]) -> None:
    for path in PATH_KEYS:
        value = lookup(raw, path)
        if value is not MISSING and not isinstance(value, str):
            raise ValueError(f"{path} must be a string")
    for path in INTEGER_TEXT_PATHS:
        value = lookup(raw, path)
        if value is not MISSING:
            positive_integer_text(value, path)
