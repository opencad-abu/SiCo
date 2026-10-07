"""Bounded, field-level comparison for DesignIR revisions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .normalize import normalize_design_ir
from .schema import DesignIR


def diff_design_ir(
    before: DesignIR | Mapping[str, Any], after: DesignIR | Mapping[str, Any]
) -> list[dict[str, Any]]:
    left = normalize_design_ir(before)
    right = normalize_design_ir(after)
    changes: list[dict[str, Any]] = []
    _walk(left, right, "", changes)
    return changes


def _walk(left: Any, right: Any, path: str, changes: list[dict[str, Any]]) -> None:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        keys = sorted(set(left) | set(right))
        for key in keys:
            child = f"{path}.{key}" if path else str(key)
            if key not in left:
                changes.append({"path": child, "kind": "added", "after": right[key]})
            elif key not in right:
                changes.append({"path": child, "kind": "removed", "before": left[key]})
            else:
                _walk(left[key], right[key], child, changes)
        return
    if isinstance(left, list) and isinstance(right, list):
        limit = max(len(left), len(right))
        for index in range(limit):
            child = f"{path}.{index}" if path else str(index)
            if index >= len(left):
                changes.append({"path": child, "kind": "added", "after": right[index]})
            elif index >= len(right):
                changes.append({"path": child, "kind": "removed", "before": left[index]})
            else:
                _walk(left[index], right[index], child, changes)
        return
    if left != right:
        changes.append({"path": path, "kind": "changed", "before": left, "after": right})


__all__ = ["diff_design_ir"]
