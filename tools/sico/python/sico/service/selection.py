"""Strict validation of the native current-cellview selection snapshot."""

from __future__ import annotations

import re

from ..core.cad_ref import normalize_item, normalize_target

SCHEMA = "cad.cv.selection.v1"
_SESSION = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WINDOW = re.compile(r"^wctx_[A-Za-z0-9_-]+_[0-9]{1,10}$")
_FIELDS = {
    "ok", "valid", "schema", "session_ref", "window_ref", "target", "items",
    "selection_count", "unsupported_count", "max_items", "offset", "truncated",
}
_CAPTURE_FIELDS = {"output", "output_truncated", "output_capture"}


def validate_selection(value, *, expected_window=None):
    if not isinstance(value, dict):
        raise ValueError("Selection snapshot must be an object")
    unknown = set(value) - _FIELDS - _CAPTURE_FIELDS
    if unknown or not _FIELDS <= set(value):
        raise ValueError("Invalid selection snapshot fields")
    if value["ok"] is not True or value["valid"] is not True:
        raise ValueError("Selection snapshot is not valid")
    if value["schema"] != SCHEMA:
        raise ValueError("Unsupported selection snapshot schema")
    if not isinstance(value["session_ref"], str) or not _SESSION.fullmatch(value["session_ref"]):
        raise ValueError("Invalid selection session")
    if not isinstance(value["window_ref"], str) or not _WINDOW.fullmatch(value["window_ref"]):
        raise ValueError("Invalid selection window reference")
    if expected_window is not None and value["window_ref"] != expected_window:
        raise ValueError("Selection snapshot target mismatch")
    target = normalize_target(value["target"])
    items = value["items"]
    if not isinstance(items, list) or len(items) > 50:
        raise ValueError("Invalid selection items")
    normalized, keys = [], set()
    for item in items:
        row = normalize_item(item)
        key = (row["kind"], tuple(row["hierarchy"]), row["name"])
        if key in keys:
            raise ValueError("Duplicate selection object")
        keys.add(key)
        normalized.append(row)
    selection_count = value["selection_count"]
    unsupported = value["unsupported_count"]
    if type(selection_count) is not int or not 0 <= selection_count <= 100000:
        raise ValueError("Invalid selection count")
    if type(unsupported) is not int or not 0 <= unsupported <= 100000:
        raise ValueError("Invalid unsupported selection count")
    if selection_count < len(normalized) or unsupported > selection_count:
        raise ValueError("Selection count does not cover its items")
    if type(value["max_items"]) is not int or not 1 <= value["max_items"] <= 50:
        raise ValueError("Invalid selection max_items")
    if type(value["offset"]) is not int or not 0 <= value["offset"] <= 4096:
        raise ValueError("Invalid selection offset")
    if type(value["truncated"]) is not bool:
        raise ValueError("Invalid selection truncation flag")
    return {
        "ok": True,
        "valid": True,
        "schema": SCHEMA,
        "session_ref": value["session_ref"],
        "window_ref": value["window_ref"],
        "target": target,
        "items": normalized,
        "selection_count": selection_count,
        "unsupported_count": unsupported,
        "max_items": value["max_items"],
        "offset": value["offset"],
        "truncated": value["truncated"],
    }


__all__ = ["SCHEMA", "validate_selection"]
