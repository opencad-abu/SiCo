"""Actionable diagnostics for MCP tool-call failures.

The model sees the JSON-RPC error message before it can retry.  This module
keeps that message self-contained: identify the tool, name the rejected
argument or tool name, show the expected shape, and say that an unchanged
retry cannot succeed.  Tool results reuse the same retry boundary while
preserving any domain-specific next action.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from typing import Any

from .circuit_spec_schema import CircuitSpecError, validate
from .mcp_error_actions import next_action
from .mcp_input_examples import attach_example

_MAX_NAMES = 24
_MAX_VALUE = 120
_CORRECTION_FIELDS = (
    "did_you_mean",
    "suggestion",
    "next_action",
    "hint",
    "allowed_workflows",
    "required_profile",
    "candidate_profiles",
    "selection_question",
    "selection_required",
    "confirmation_required",
    "missing",
)


def _shape(schema: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    required = [name for name in schema.get("required", ()) if isinstance(name, str)]
    allowed = [name for name in schema.get("properties", {}) if isinstance(name, str)]
    return required[:_MAX_NAMES], allowed[:_MAX_NAMES]


def _names(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _received(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"boolean {value}"
    if isinstance(value, (int, float)):
        return f"number {value}"
    if isinstance(value, str):
        shown = value if len(value) <= _MAX_VALUE else value[:_MAX_VALUE - 3] + "..."
        return f"string {shown!r}"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _bounded_name(value: str) -> str:
    return value if len(value) <= _MAX_VALUE else value[:_MAX_VALUE - 3] + "..."


def _did_you_mean(value: str, candidates: list[str]) -> list[str]:
    return difflib.get_close_matches(value, candidates, n=3, cutoff=0.55)


def unknown_tool(name: str, available: list[str]) -> tuple[str, dict[str, Any]]:
    """Explain an unknown call name and suggest bounded alternatives."""
    bounded = _bounded_name(name)
    suggestions = _did_you_mean(name, available)
    hint = (
        " Did you mean "
        + ", ".join(repr(item) for item in suggestions)
        + "?"
        if suggestions
        else ""
    )
    message = (
        f"Unknown MCP tool {bounded!r}.{hint} No tool ran. "
        "Use an exact name from tools/list (or mcp_help action=list); "
        "do not repeat this name unchanged."
    )
    data = {
        "code": "unknown_tool",
        "tool": bounded,
        "did_you_mean": suggestions,
        "retry_same_request": False,
        "next_action": "Choose an exact advertised tool name before calling again",
    }
    return message, data


def unavailable_tool(
    name: str, profile: str, available: list[str]
) -> tuple[str, dict[str, Any]]:
    """Explain that an existing tool is denied by the active profile."""
    bounded = _bounded_name(name)
    suggestions = _did_you_mean(name, available)
    hint = (
        " Available names closest to this call: "
        + ", ".join(repr(item) for item in suggestions)
        + "."
        if suggestions
        else ""
    )
    message = (
        f"MCP tool {bounded!r} is registered but unavailable in profile {profile!r}. "
        f"No tool ran.{hint} Use a tool advertised for this profile; "
        "changing arguments cannot make a denied tool available."
    )
    data = {
        "code": "tool_not_available",
        "tool": bounded,
        "profile": profile,
        "did_you_mean": suggestions,
        "retry_same_request": False,
        "next_action": "Use a tool advertised for the active profile",
    }
    return message, data


def invalid_call_shape(
    message: str,
    *,
    expected: str,
    received: Any,
    code: str = "invalid_call",
    tool_name: str | None = None,
    schema: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Name the malformed MCP envelope field and the expected shape."""
    full = (
        f"{message}; expected {expected}, got {_received(received)}. "
        "No tool ran. Correct the call envelope and send it once more."
    )
    data = {
        "code": code,
        "message": full,
        "expected": expected,
        "received_type": _received(received),
        "retry_same_request": False,
        "next_action": "Correct the MCP call envelope",
    }
    if tool_name is not None:
        data["tool"] = tool_name
    attach_example(data, tool_name, schema)
    return data["message"], data


def invalid_arguments(
    tool_name: str, schema: Mapping[str, Any], arguments: Any
) -> tuple[str, dict[str, Any]] | None:
    """Validate one argument object and return a correction-ready failure."""
    required, allowed = _shape(schema)
    try:
        validate(arguments, schema)
    except CircuitSpecError as exc:
        detail = str(exc)
    else:
        return None
    parts = [
        f"Tool {tool_name!r} rejected its arguments: {detail}.",
        "No tool action ran.",
    ]
    if required:
        parts.append("Required top-level fields: " + _names(required) + ".")
    if allowed and "allowed fields:" not in detail:
        parts.append("Allowed top-level fields: " + _names(allowed) + ".")
    parts.append(
        "Correct the named field(s) and call again with the complete arguments "
        "object; do not repeat the unchanged request."
    )
    message = " ".join(parts)
    data = {
        "code": "invalid_arguments",
        "message": message,
        "tool": tool_name,
        "required": required,
        "allowed": allowed,
        "detail": detail,
        "retry_same_request": False,
        "next_action": "Correct the named argument(s), then call again",
    }
    attach_example(data, tool_name, schema)
    return data["message"], data


def rejected_tool_call(
    tool_name: str,
    detail: str,
    schema: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Wrap an executor-declared argument/precondition rejection."""
    message = (
        f"Tool {tool_name!r} rejected the request: {detail}. "
        "No successful result was produced. Correct the named input or state "
        "before invoking it again; do not repeat the unchanged request."
    )
    data = {
        "code": "invalid_tool_arguments",
        "message": message,
        "tool": tool_name,
        "detail": detail,
        "retry_same_request": False,
        "next_action": "Correct the named input or state before retrying",
    }
    attach_example(data, tool_name, schema)
    return data["message"], data


def normalize_tool_result(
    detail: Any,
    tool_name: str,
    schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Give every failed tool result a stable, non-retryable envelope.

    Domain handlers keep ownership of their specific ``message``,
    ``next_action``, ``hint`` and ``did_you_mean`` fields.  This function only
    fills missing identity and retry fields so a model never receives an
    anonymous or silently retryable failure.
    """
    if not isinstance(detail, Mapping):
        detail = {"message": str(detail)}
    result = dict(detail)
    # This function is called only for failed calls.  Do not preserve a
    # contradictory success flag from an older envelope.
    result["ok"] = False
    result.setdefault("tool", tool_name)
    nested = result.get("data")
    if isinstance(nested, Mapping):
        for field in _CORRECTION_FIELDS:
            if field in nested:
                result.setdefault(field, nested[field])
    if not isinstance(result.get("code"), str) or not result["code"]:
        error = result.get("error")
        code = error.get("code") if isinstance(error, Mapping) else None
        result["code"] = code if isinstance(code, str) and code else "tool_error"
    message = result.get("message")
    if not isinstance(message, str) or not message:
        error = result.get("error")
        if isinstance(error, str) and error:
            message = error
        elif isinstance(error, Mapping) and isinstance(error.get("message"), str):
            message = error["message"]
        elif isinstance(result.get("diagnostic"), str) and result["diagnostic"]:
            message = result["diagnostic"]
        elif isinstance(result.get("reason"), str) and result["reason"]:
            message = result["reason"]
        else:
            message = f"{tool_name} returned an error without a diagnostic"
        result["message"] = message
    result.setdefault("next_action", next_action(result["code"]))
    result.setdefault("retry_same_request", False)
    attach_example(result, tool_name, schema)
    return result


__all__ = [
    "invalid_arguments",
    "invalid_call_shape",
    "normalize_tool_result",
    "rejected_tool_call",
    "unavailable_tool",
    "unknown_tool",
]
