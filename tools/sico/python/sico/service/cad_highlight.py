"""Validation for the native object-location (view-effect) response."""

from __future__ import annotations

import re

from ..core.cad_ref import normalize_bbox, normalize_target

SCHEMA = "cad.cv.highlight.v1"
_SESSION = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WINDOW = re.compile(r"^wctx_[A-Za-z0-9_-]+_[0-9]{1,10}$")
_FIELDS = {
    "ok", "valid", "schema", "session_ref", "target", "kind", "name",
    "window_ref", "highlighted", "highlight_count", "zoomed", "selection_preserved",
}
_OPTIONAL = {"bbox"}
_CAPTURE_FIELDS = {"output", "output_truncated", "output_capture"}


def _name(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("Invalid highlighted object name")
    return value


def validate_highlight(value):
    if not isinstance(value, dict):
        raise ValueError("Highlight result must be an object")
    unknown = set(value) - _FIELDS - _OPTIONAL - _CAPTURE_FIELDS
    if unknown or not _FIELDS <= set(value):
        raise ValueError("Invalid highlight result fields")
    if value["ok"] is not True or value["valid"] is not True:
        raise ValueError("Highlight result is not valid")
    if value["schema"] != SCHEMA:
        raise ValueError("Unsupported highlight result schema")
    if not isinstance(value["session_ref"], str) or not _SESSION.fullmatch(value["session_ref"]):
        raise ValueError("Invalid highlight session")
    if value["kind"] not in {"instance", "net", "terminal", "shape", "cellview"}:
        raise ValueError("Unsupported highlighted object kind")
    name = _name(value["name"])
    window_ref = value["window_ref"]
    if window_ref is not None and (
        not isinstance(window_ref, str) or not _WINDOW.fullmatch(window_ref)
    ):
        raise ValueError("Invalid highlight window reference")
    if type(value["highlighted"]) is not bool or type(value["selection_preserved"]) is not bool:
        raise ValueError("Invalid highlight flags")
    if type(value["highlight_count"]) is not int or value["highlight_count"] < 0:
        raise ValueError("Invalid highlight count")
    if value["kind"] == "cellview":
        if value["highlighted"] is not False or value["highlight_count"] != 0:
            raise ValueError("Cellview location cannot claim an object highlight")
    elif value["highlighted"] is not True or value["highlight_count"] < 1:
        raise ValueError("Object location did not highlight an object")
    if type(value["zoomed"]) is not bool:
        raise ValueError("Invalid highlight zoom flag")
    return {
        "ok": True,
        "valid": True,
        "schema": SCHEMA,
        "session_ref": value["session_ref"],
        "target": normalize_target(value["target"]),
        "kind": value["kind"],
        "name": name,
        "window_ref": window_ref,
        "highlighted": value["highlighted"],
        "highlight_count": value["highlight_count"],
        "zoomed": value["zoomed"],
        "selection_preserved": value["selection_preserved"],
        **({"bbox": normalize_bbox(value["bbox"])} if "bbox" in value else {}),
    }


__all__ = ["SCHEMA", "validate_highlight"]
