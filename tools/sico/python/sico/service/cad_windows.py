"""Validation for windows displaying one durable CAD target."""

from __future__ import annotations

import re

from ..core.cad_ref import normalize_target
from ..core.window_refs import normalize_window_list

SCHEMA = "cad.cv.windows.v1"
_SESSION = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WINDOW = re.compile(r"^wctx_[A-Za-z0-9_-]+_[0-9]{1,10}$")
_TITLE_BYTES = re.compile(r"(?:[0-9a-f]{2}){0,4096}")


def validate_windows(value):
    if not isinstance(value, dict):
        raise ValueError("Window list must be an object")
    if set(value) != {"ok", "valid", "schema", "session_ref", "target", "windows"}:
        raise ValueError("Invalid window list fields")
    if value["ok"] is not True or value["valid"] is not True:
        raise ValueError("Window list is not valid")
    if value["schema"] != SCHEMA:
        raise ValueError("Unsupported window list schema")
    if not isinstance(value["session_ref"], str) or not _SESSION.fullmatch(value["session_ref"]):
        raise ValueError("Invalid window list session")
    rows = value["windows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise ValueError("Invalid window list rows")
    windows = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - {
            "window_ref", "window", "title", "title_bytes_hex",
        }:
            raise ValueError("Invalid window row")
        ref = row.get("window_ref")
        if ref is not None and (not isinstance(ref, str) or not _WINDOW.fullmatch(ref)):
            raise ValueError("Invalid window reference")
        if not isinstance(row.get("window"), str) or not row["window"]:
            raise ValueError("Invalid window identity")
        raw_title = row.get("title_bytes_hex")
        if raw_title is not None:
            if not isinstance(raw_title, str) or not _TITLE_BYTES.fullmatch(raw_title):
                raise ValueError("Invalid window title bytes")
            try:
                title = bytes.fromhex(raw_title).decode("utf-8")
            except UnicodeDecodeError:
                title = None
        else:
            title = row.get("title")
        if title is not None and (
            not isinstance(title, str) or len(title) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in title)
        ):
            raise ValueError("Invalid window title")
        windows.append({"window_ref": ref, "window": row["window"], "title": title})
    return {
        "ok": True,
        "valid": True,
        "schema": SCHEMA,
        "session_ref": value["session_ref"],
        "target": normalize_target(value["target"]),
        "windows": windows,
    }


def validate_window_list(value):
    """Validate the all-window listing used by the multi-window @ picker."""
    return normalize_window_list(value)


__all__ = ["SCHEMA", "validate_window_list", "validate_windows"]
