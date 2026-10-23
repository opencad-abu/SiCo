"""Validation for the bounded current-cellview object list."""

from __future__ import annotations

import re

from ..core.cad_ref import normalize_item, normalize_target

SCHEMA = "cad.cv.objects.v1"
_SESSION = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WINDOW = re.compile(r"^wctx_[A-Za-z0-9_-]+_[0-9]{1,10}$")
_FIELDS = {
    "ok", "valid", "schema", "session_ref", "window_ref", "target", "items",
    "total", "max_items", "offset", "truncated", "selection_count",
}
_CAPTURE_FIELDS = {"output", "output_truncated", "output_capture"}


def validate_object_list(value, *, expected_window=None):
    if not isinstance(value, dict):
        raise ValueError("Object list must be an object")
    if set(value) - _FIELDS - _CAPTURE_FIELDS or not _FIELDS <= set(value):
        raise ValueError("Invalid object list fields")
    if value["ok"] is not True or value["valid"] is not True:
        raise ValueError("Object list is not valid")
    if value["schema"] != SCHEMA:
        raise ValueError("Unsupported object list schema")
    if not isinstance(value["session_ref"], str) or not _SESSION.fullmatch(value["session_ref"]):
        raise ValueError("Invalid object list session")
    if not isinstance(value["window_ref"], str) or not _WINDOW.fullmatch(value["window_ref"]):
        raise ValueError("Invalid object list window")
    if expected_window is not None and value["window_ref"] != expected_window:
        raise ValueError("Object list target mismatch")
    items = value["items"]
    if not isinstance(items, list) or len(items) > 50:
        raise ValueError("Invalid object list items")
    normalized = [normalize_item(item) for item in items]
    total = value["total"]
    if type(total) is not int or not len(normalized) <= total <= 100000:
        raise ValueError("Invalid object list total")
    if type(value["max_items"]) is not int or not 1 <= value["max_items"] <= 50:
        raise ValueError("Invalid object list limit")
    if type(value["offset"]) is not int or not 0 <= value["offset"] <= 4096:
        raise ValueError("Invalid object list offset")
    if type(value["truncated"]) is not bool:
        raise ValueError("Invalid object list truncation")
    if type(value["selection_count"]) is not int or not 0 <= value["selection_count"] <= 100000:
        raise ValueError("Invalid object list selection count")
    return {
        "ok": True,
        "valid": True,
        "schema": SCHEMA,
        "session_ref": value["session_ref"],
        "window_ref": value["window_ref"],
        "target": normalize_target(value["target"]),
        "items": normalized,
        "total": total,
        "max_items": value["max_items"],
        "offset": value["offset"],
        "truncated": value["truncated"],
        "selection_count": value["selection_count"],
    }


__all__ = ["SCHEMA", "validate_object_list"]
