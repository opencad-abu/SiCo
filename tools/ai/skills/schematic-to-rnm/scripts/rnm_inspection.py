"""Validate bounded inspection envelopes, identities and primitive fields."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "cad.schematic-to-rnm.normalized-netlist.v1"


TOOL_VERSION = "20260829.m0.v1"


REQUIRED_SCHEMATIC_KEYS = ("instances", "nets", "terminals")


REQUIRED_SYMBOL_KEYS = ("terminals",)


class BlockedInput(ValueError):
    """Raised when the source cannot be trusted as a complete structure."""

    def __init__(self, message: str, *, code: str = "incomplete_inspection") -> None:
        super().__init__(message)
        self.code = code


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BlockedInput(f"cannot read valid JSON from {path}: {exc}", code="invalid_json")
    value = _unwrap_mcp(value)
    if not isinstance(value, dict):
        raise BlockedInput(f"{path} must contain a JSON object", code="invalid_shape")
    return value


def _unwrap_mcp(value: Any) -> Any:
    """Accept raw tool JSON and an MCP tools/call response envelope."""

    if not isinstance(value, dict):
        return value
    result = value.get("result")
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list) and content:
            text = content[0].get("text") if isinstance(content[0], dict) else None
            if isinstance(text, str):
                try:
                    return _unwrap_mcp(json.loads(text))
                except json.JSONDecodeError:
                    return value
    if isinstance(value.get("content"), list) and value["content"]:
        text = value["content"][0].get("text") if isinstance(value["content"][0], dict) else None
        if isinstance(text, str):
            try:
                return _unwrap_mcp(json.loads(text))
            except json.JSONDecodeError:
                return value
    return value


def _require_ok(payload: Mapping[str, Any], kind: str, required: Sequence[str], path: Path) -> None:
    if payload.get("ok") is not True:
        error = payload.get("error")
        detail = error.get("message") if isinstance(error, dict) else "inspection returned ok=false"
        raise BlockedInput(f"{path}: {detail}", code="inspection_error")
    if payload.get("kind") != kind:
        raise BlockedInput(
            f"{path}: expected kind={kind!r}, got {payload.get('kind')!r}",
            code="wrong_inspection_kind",
        )
    if payload.get("truncated") is not False:
        raise BlockedInput(
            f"{path}: inspection must explicitly report truncated=false",
            code="truncated_inspection",
        )
    for key in required:
        if not isinstance(payload.get(key), list):
            raise BlockedInput(f"{path}: missing complete list {key!r}", code="missing_field")


def _text(value: Any, field: str, subject: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise BlockedInput(f"{subject}: missing {field}", code="missing_field")
        return None
    if not isinstance(value, str) or not value.strip():
        raise BlockedInput(f"{subject}: {field} must be a non-empty string", code="invalid_field")
    return value


def _integer(value: Any, field: str, subject: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise BlockedInput(f"{subject}: {field} must be an integer", code="invalid_field")
    return value


def _as_dicts(value: Any, field: str, subject: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise BlockedInput(f"{subject}: {field} must be a list", code="missing_field")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise BlockedInput(
                f"{subject}: {field}[{index}] must be an object", code="invalid_shape"
            )
        result.append(item)
    return result


def _cellview(payload: Mapping[str, Any], subject: str) -> dict[str, str | None]:
    cellview = payload.get("cellview")
    if not isinstance(cellview, dict):
        raise BlockedInput(f"{subject}: missing cellview identity", code="missing_field")
    return {
        "lib": _text(cellview.get("lib"), "cellview.lib", subject, required=True),
        "cell": _text(cellview.get("cell"), "cellview.cell", subject, required=True),
        "view": _text(cellview.get("view"), "cellview.view", subject, required=True),
    }
