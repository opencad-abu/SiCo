"""Normalization for the bounded all-window inventory used by @ pickers."""

from __future__ import annotations

import re

from .cad_ref import normalize_target

_SESSION = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
_WINDOW = re.compile(r"^wctx_[A-Za-z0-9_-]+_[0-9]{1,10}$")
_TITLE_BYTES = re.compile(r"(?:[0-9a-f]{2}){0,4096}")
_FIELDS = {
    "ok", "valid", "schema", "session_ref", "pid", "windows", "max_items",
    "total", "offset", "next_offset", "truncated", "snapshot_atomic", "read_scope",
}
_ROW_FIELDS = {
    "id", "window_ref", "kind", "window_class", "application", "title_bytes_hex",
    "parent_id", "is_current", "display_state", "screen_bbox",
    "screen_coordinates", "visible_design_bbox", "design_coordinates",
    "cellview", "display_cellview", "edit_cellview", "edit_hierarchy",
    "selection_count", "ade_session", "maestro",
}


def normalize_window_list(value):
    """Normalize the bounded all-window listing returned by Virtuoso."""
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("Invalid window list fields")
    if value["ok"] is not True or value["valid"] is not True:
        raise ValueError("Window list is not valid")
    if value["schema"] != "cad.cv.windows.v1":
        raise ValueError("Unsupported window list schema")
    if not isinstance(value["session_ref"], str) or not _SESSION.fullmatch(value["session_ref"]):
        raise ValueError("Invalid window list session")
    if type(value["pid"]) is not int or value["pid"] < 1:
        raise ValueError("Invalid window list process")
    if not isinstance(value["windows"], list) or len(value["windows"]) > 200:
        raise ValueError("Invalid window list rows")
    if type(value["total"]) is not int or value["total"] < 0:
        raise ValueError("Invalid window list total")
    if type(value["max_items"]) is not int or not 1 <= value["max_items"] <= 200:
        raise ValueError("Invalid window list limit")
    if type(value["offset"]) is not int or not 0 <= value["offset"] <= 4096:
        raise ValueError("Invalid window list offset")
    if type(value["truncated"]) is not bool:
        raise ValueError("Invalid window list truncation")
    windows = []
    for row in value["windows"]:
        if not isinstance(row, dict) or set(row) - _ROW_FIELDS:
            raise ValueError("Invalid window list row")
        ref = row.get("window_ref")
        window = row.get("window") or row.get("id")
        if not isinstance(ref, str) or not _WINDOW.fullmatch(ref):
            raise ValueError("Invalid window list reference")
        if not isinstance(window, str) or not window:
            raise ValueError("Invalid window list identity")
        cellview = row.get("cellview")
        if not isinstance(cellview, dict):
            continue
        target = normalize_target({
            "library": cellview.get("lib"),
            "cell": cellview.get("cell"),
            "view": cellview.get("view"),
            "view_type": cellview.get("view_type"),
        })
        title = None
        raw = row.get("title_bytes_hex")
        if isinstance(raw, str) and _TITLE_BYTES.fullmatch(raw):
            try:
                title = bytes.fromhex(raw).decode("utf-8")
            except UnicodeDecodeError:
                title = None
        windows.append({
            "window_ref": ref,
            "window": window,
            "title": title,
            "kind": row.get("kind") if isinstance(row.get("kind"), str) else "other",
            "target": target,
            "is_current": row.get("is_current") is True,
        })
    return {
        "session_ref": value["session_ref"],
        "windows": windows,
        "total": value["total"],
        "truncated": value["truncated"],
    }


__all__ = ["normalize_window_list"]
