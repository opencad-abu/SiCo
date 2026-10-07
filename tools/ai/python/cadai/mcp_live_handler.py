"""MCP handler for the bounded live-model protocol."""

from __future__ import annotations

import math
from typing import Any

from .live_model import LiveModelProtocolError, parse_live_message


def _live_tool(
    name: str,
    title: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
    *,
    read_only: bool,
    approval_required: bool,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object", "properties": properties, "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return {
        "name": name, "title": title, "description": description,
        "inputSchema": schema,
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": not read_only,
            "idempotentHint": read_only,
            "openWorldHint": not read_only,
            "approvalRequired": approval_required,
        },
    }


LIVE_MODEL_TOOLS = [
    _live_tool(
        "live_model_start", "Start live modeling",
        "Start one controller-owned saved-source modeling session.",
        {
            "recipe_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,159}$"},
            "through": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,159}$"},
            "profile": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,159}$"},
            "debounce_ms": {"type": "integer", "minimum": 0, "maximum": 10000},
            "target": {"type": "object", "properties": {"library": {"type": "string"}, "cell": {"type": "string"}, "module": {"type": "string"}}, "required": ["library", "cell", "module"], "additionalProperties": False},
            "view_identity": {"type": "object", "properties": {"library": {"type": "string"}, "cell": {"type": "string"}, "view": {"type": "string"}, "view_type": {"type": "string"}, "kind": {"type": "string"}}, "required": ["library", "cell", "view", "view_type", "kind"], "additionalProperties": False},
        }, ["recipe_id", "through", "profile"], read_only=False, approval_required=True,
    ),
    _live_tool(
        "live_model_source_changed", "Queue saved source",
        "Queue one bounded post-save source generation for the active live session.",
        {
            "sequence": {"type": "integer", "minimum": 1},
            "source_generation": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,255}$"},
            "target": {"type": "object", "properties": {"library": {"type": "string"}, "cell": {"type": "string"}, "module": {"type": "string"}}, "required": ["library", "cell", "module"], "additionalProperties": False},
            "view_identity": {"type": "object", "properties": {"library": {"type": "string"}, "cell": {"type": "string"}, "view": {"type": "string"}, "view_type": {"type": "string"}, "kind": {"type": "string"}}, "required": ["library", "cell", "view", "view_type", "kind"], "additionalProperties": False},
            "changed_kind": {"type": "string"},
        }, ["sequence", "source_generation", "target", "view_identity", "changed_kind"],
        read_only=False, approval_required=True,
    ),
    _live_tool("live_model_status", "Live modeling status", "Read the bounded live modeling state.", {}, read_only=True, approval_required=False),
    _live_tool("live_model_events", "Live modeling events", "Read bounded live modeling event history.", {"limit": {"type": "integer", "minimum": 1, "maximum": 128}}, read_only=True, approval_required=False),
    _live_tool("live_model_stop", "Stop live modeling", "Stop the active live modeling worker.", {}, read_only=False, approval_required=True),
    _live_tool("live_model_approve_publish", "Approve publication", "Record a human publication request; this M2 bridge has no publisher and will refuse OA writes.", {"approval_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,159}$"}}, ["approval_id"], read_only=False, approval_required=True),
    _live_tool("live_model_cancel_publish", "Cancel publication", "Cancel a pending publication request without modifying Virtuoso.", {}, read_only=False, approval_required=True),
]
LIVE_NAMES = frozenset(tool["name"] for tool in LIVE_MODEL_TOOLS)


class LiveHandlerArgumentError(ValueError):
    """Raised when a live-model request violates protocol constraints."""


def dispatch_live(name: str, arguments: dict[str, Any], *, client: Any) -> tuple[bool, dict[str, Any]]:
    if name not in LIVE_NAMES:
        raise ValueError(f"unknown live-model tool: {name}")
    try:
        checked = _validated(name, arguments)
        ok, detail = client.call(name, checked)
        return ok, _sanitize(detail)
    except LiveModelProtocolError as exc:
        raise LiveHandlerArgumentError(str(exc)) from exc
    except (OSError, ValueError) as exc:
        return False, {"code": "live_model_error", "message": str(exc)}


def _validated(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate the MCP live tool envelope before sending it to Virtuoso."""
    event_by_tool = {
        "live_model_start": "live_model.start",
        "live_model_source_changed": "live_model.source_changed",
        "live_model_approve_publish": "live_model.approve_publish",
    }
    event = event_by_tool.get(name)
    if event is not None:
        value = dict(arguments)
        value["event"] = event
        checked = parse_live_message(value)
        checked.pop("event", None)
        return checked
    if name == "live_model_events":
        if set(arguments) - {"limit"}:
            raise LiveModelProtocolError("unknown_field", "events accepts only limit")
        limit = arguments.get("limit", 32)
        # Parse through the strict event schema for the common bounded request.
        parse_live_message({"event": "live_model.status"})
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 128:
            raise LiveModelProtocolError("invalid_number", "event limit is outside the bounded range")
        return {"limit": limit}
    if name in {"live_model_status", "live_model_stop", "live_model_cancel_publish"}:
        if arguments:
            raise LiveModelProtocolError("unknown_field", "%s takes no parameters" % name.removeprefix("live_model_"))
        parse_live_message({"event": {
            "live_model_status": "live_model.status",
            "live_model_stop": "live_model.stop",
            "live_model_cancel_publish": "live_model.cancel_publish",
        }[name]})
        return {}
    raise LiveModelProtocolError("invalid_event", "unsupported live-model tool")


def _sanitize(value: Any, *, depth: int = 0) -> Any:
    """Copy a controller response without allowing secrets or verdicts out."""
    if depth > 8:
        raise LiveModelProtocolError("response_too_deep", "live-model response is too deeply nested")
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise LiveModelProtocolError("invalid_response", "live-model response keys must be text")
            lowered = key.casefold()
            if any(word in lowered for word in ("token", "credential", "password", "secret", "api_key", "authorization")):
                raise LiveModelProtocolError("credential_forbidden", "live-model response contains a credential field")
            if lowered in {"verdict", "provider_verdict", "provider_status", "provider_decision", "decision"}:
                raise LiveModelProtocolError("verdict_forbidden", "provider verdict fields are not allowed")
            result[key] = _sanitize(child, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > 128:
            raise LiveModelProtocolError("response_too_large", "live-model response list is too large")
        return [_sanitize(child, depth=depth + 1) for child in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise LiveModelProtocolError("invalid_response", "live-model response contains a non-finite number")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise LiveModelProtocolError("invalid_response", "live-model response is not JSON-shaped")
