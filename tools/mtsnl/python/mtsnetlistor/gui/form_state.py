"""Qt-free helpers for editable MTS request form state.

The GUI deliberately keeps widgets out of the request model.  These small
helpers make row ordering and text-to-value conversions deterministic and
allow the important form behaviour to be tested on headless CI hosts where
PyQt5 is not installed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar


T = TypeVar("T")


def move_row(rows: Sequence[T], index: int, delta: int) -> tuple[T, ...]:
    """Return *rows* with one row moved by ``delta`` positions.

    Invalid indexes and moves beyond either end are no-ops.  The input is
    never mutated, which is useful both for GUI undo-like operations and for
    unit tests.
    """

    result = list(rows)
    if not 0 <= index < len(result) or delta == 0:
        return tuple(result)
    target = max(0, min(len(result) - 1, index + delta))
    if target == index:
        return tuple(result)
    item = result.pop(index)
    result.insert(target, item)
    return tuple(result)


def duplicate_row(rows: Sequence[T], index: int) -> tuple[T, ...]:
    """Duplicate one row immediately after itself, or return a no-op."""

    result = list(rows)
    if not 0 <= index < len(result):
        return tuple(result)
    result.insert(index + 1, result[index])
    return tuple(result)


def remove_rows(rows: Sequence[T], indexes: Sequence[int]) -> tuple[T, ...]:
    """Remove selected row indexes while preserving the remaining order."""

    selected = {index for index in indexes if 0 <= index < len(rows)}
    return tuple(row for index, row in enumerate(rows) if index not in selected)


def parse_enum_values(text: str) -> tuple[str, ...]:
    """Parse the GUI's comma-separated enum field.

    Empty entries are ignored and duplicate values are removed while keeping
    the first occurrence.  The simulator option validator can then provide a
    precise error for a selected value that is not in this list.
    """

    values: list[str] = []
    for raw in str(text).split(","):
        value = raw.strip()
        if value and value not in values:
            values.append(value)
    return tuple(values)


def optional_number(value: float, *, unset: float | None) -> float | None:
    """Map a spin-box value to ``None`` when its explicit sentinel is used."""

    if unset is not None and value == unset:
        return None
    return float(value)


__all__ = [
    "duplicate_row",
    "move_row",
    "optional_number",
    "parse_enum_values",
    "remove_rows",
]
