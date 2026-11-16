"""Bidirectional adapters between DataHub envelopes and existing CAD DTOs."""

from __future__ import annotations

from typing import Optional

from sico.core.cad_ref import normalize_ref
from sico.service.cad_highlight import validate_highlight
from sico.service.cad_object_list import validate_object_list
from sico.service.cad_windows import validate_window_list
from sico.service.selection import validate_selection

from .contracts import LEGACY_ERRORS
from .envelope import Envelope, Meta, Page
from .errors import ERROR_CODES, HubError
from .registry import Registry, default_registry

_MODES = {
    ("sico.cad.selection.v1", "inspect"): "selection",
    ("sico.cad.objects.v1", "list"): "objects",
    ("sico.cad.windows.v1", "list"): "windows",
    ("sico.cad.highlight.v1", "highlight"): "highlight",
    ("sico.ask.objects.v1", "selection"): "selection",
    ("sico.ask.objects.v1", "objects"): "objects",
    ("sico.ask.objects.v1", "windows"): "windows",
}
PILOT_ACTIONS = tuple(sorted(_MODES))

def _mode(contract, action):
    mode = _MODES.get((contract, action))
    if mode is None:
        raise HubError("unsupported_contract", "Contract action is not a CAD pilot")
    return mode


def _safe_message(value, fallback):
    source = value if isinstance(value, str) and value else fallback
    text = "".join(
        " " if ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF
        else char
        for char in source
    )
    return text[:4096] or "CAD operation failed"


def _hub_error(code):
    mapped = code if code in ERROR_CODES else LEGACY_ERRORS.get(code, "internal")
    return mapped, {"legacy_code": code}


def _error_value(value):
    code = value.get("code")
    if not isinstance(code, str) or not code:
        code = "internal"
    hub_code, details = _hub_error(code)
    return hub_code, _safe_message(value.get("message"), code), details


def _next_offset(offset, item_count, total, truncated):
    if not truncated:
        return None
    candidate = max(offset + item_count, offset + 1)
    return min(candidate, total) if total > offset else offset + 1


def _selection(value):
    data = validate_selection(value)
    page = Page(
        offset=data["offset"],
        limit=data["max_items"],
        total=data["selection_count"],
        next=_next_offset(
            data["offset"],
            len(data["items"]),
            data["selection_count"],
            data["truncated"],
        ),
        truncated=data["truncated"],
    )
    payload = {
        key: data[key]
        for key in (
            "session_ref",
            "window_ref",
            "target",
            "items",
            "selection_count",
            "unsupported_count",
        )
    }
    return payload, page


def _objects(value):
    data = validate_object_list(value)
    page = Page(
        offset=data["offset"],
        limit=data["max_items"],
        total=data["total"],
        next=_next_offset(
            data["offset"], len(data["items"]), data["total"], data["truncated"]
        ),
        truncated=data["truncated"],
    )
    payload = {
        key: data[key]
        for key in (
            "session_ref",
            "window_ref",
            "target",
            "items",
            "selection_count",
        )
    }
    return payload, page


def _windows(value):
    validate_window_list(value)
    next_offset = value.get("next_offset") if value["truncated"] else None
    if value["truncated"] and type(next_offset) is not int:
        next_offset = value["offset"] + value["max_items"]
    page = Page(
        offset=value["offset"],
        limit=value["max_items"],
        total=value["total"],
        next=next_offset,
        truncated=value["truncated"],
    )
    payload = {
        key: value[key]
        for key in ("session_ref", "pid", "snapshot_atomic", "read_scope", "windows")
    }
    return payload, page


def _highlight(value):
    data = validate_highlight(value)
    return {
        key: data[key]
        for key in data
        if key not in {"ok", "valid", "schema"}
    }, None


def from_legacy(
    contract: str,
    action: str,
    request_id: str,
    value: dict,
    *,
    meta: Optional[Meta] = None,
) -> Envelope:
    """Adapt one existing CAD success/error DTO to an envelope."""
    _mode(contract, action)
    if not isinstance(value, dict):
        raise HubError("invalid_arguments", "Legacy CAD DTO must be an object")
    if value.get("ok") is False:
        code, message, details = _error_value(value)
        return Envelope.failure(
            contract, action, request_id, code, message, details=details, meta=meta
        )
    payload, page = {
        "selection": _selection,
        "objects": _objects,
        "windows": _windows,
        "highlight": _highlight,
    }[_mode(contract, action)](value)
    return Envelope(
        contract=contract,
        action=action,
        id=request_id,
        payload=payload,
        page=page,
        meta=meta or Meta(),
    )


def _legacy_selection(envelope):
    if envelope.page is None:
        raise HubError("invalid_arguments", "Selection requires a page")
    payload = dict(envelope.payload)
    return {
        "ok": True,
        "valid": True,
        "schema": "cad.cv.selection.v1",
        **payload,
        "max_items": envelope.page.limit,
        "offset": envelope.page.offset,
        "truncated": envelope.page.truncated,
    }


def _legacy_objects(envelope):
    if envelope.page is None:
        raise HubError("invalid_arguments", "Object list requires a page")
    payload = dict(envelope.payload)
    return {
        "ok": True,
        "valid": True,
        "schema": "cad.cv.objects.v1",
        **payload,
        "total": envelope.page.total,
        "max_items": envelope.page.limit,
        "offset": envelope.page.offset,
        "truncated": envelope.page.truncated,
    }


def _legacy_windows(envelope):
    if envelope.page is None:
        raise HubError("invalid_arguments", "Window list requires a page")
    payload = dict(envelope.payload)
    return {
        "ok": True,
        "valid": True,
        "schema": "cad.windows.v1",
        **payload,
        "max_items": envelope.page.limit,
        "total": envelope.page.total,
        "offset": envelope.page.offset,
        "next_offset": envelope.page.next,
        "truncated": envelope.page.truncated,
    }


def _legacy_highlight(envelope):
    if envelope.page is not None:
        raise HubError("invalid_arguments", "Highlight results cannot be paged")
    return {
        "ok": True,
        "valid": True,
        "schema": "cad.cv.highlight.v1",
        **dict(envelope.payload),
    }


def to_legacy(envelope: Envelope) -> dict:
    """Convert an envelope back to the existing CAD DTO shape."""
    mode = _mode(envelope.contract, envelope.action)
    if envelope.status == "unknown":
        raise HubError("timeout", "Unknown outcomes cannot be mapped to a legacy DTO")
    if envelope.status == "error":
        assert envelope.error is not None
        legacy = envelope.error.details.get("legacy_code")
        code = legacy if isinstance(legacy, str) and legacy else envelope.error.code
        return {"ok": False, "code": code, "message": envelope.error.message}
    value = {
        "selection": _legacy_selection,
        "objects": _legacy_objects,
        "windows": _legacy_windows,
        "highlight": _legacy_highlight,
    }[mode](envelope)
    {
        "selection": validate_selection,
        "objects": validate_object_list,
        "windows": validate_window_list,
        "highlight": validate_highlight,
    }[mode](value)
    return value


def request(
    contract: str,
    action: str,
    request_id: str,
    payload: dict,
    *,
    meta: Optional[Meta] = None,
    registry: Optional[Registry] = None,
) -> Envelope:
    """Build a request envelope and enforce its contract request fields."""
    _mode(contract, action)
    envelope = Envelope(
        contract=contract,
        action=action,
        id=request_id,
        payload=payload,
        meta=meta or Meta(),
    )
    (registry or default_registry()).validate_request(envelope)
    return envelope


def validate_object_reference(value: dict) -> dict:
    """Validate the standalone cad.object_ref.v1 data contract."""
    return normalize_ref(value)


__all__ = ["PILOT_ACTIONS", "from_legacy", "request", "to_legacy", "validate_object_reference"]
