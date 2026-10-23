"""Compact MCP wire projection for large, structurally nested circuit tools.

``inputSchema`` remains the authoritative validation contract.  The MCP list
only needs enough information for a model to choose a tool and provide its
top-level arguments; the exact nested schema is available through
``mcp_help action=schema``.  This projection is deliberately limited to the
circuit/template tools whose nested schemas exceed the structure budget.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

COMPACT_TOOLS = frozenset({
    "prepare_circuit_creation",
    "prepare_circuit_edit",
    "prepare_circuit_template_plan",
    "preview_circuit_geometry",
    "preview_circuit_spec",
    "preview_template_placement",
})


def _summary_property(schema: Any) -> dict[str, Any]:
    """Keep a useful top-level hint without advertising the nested contract."""
    if not isinstance(schema, Mapping):
        return {"type": "object", "additionalProperties": True}
    kind = schema.get("type")
    if kind == "array":
        result: dict[str, Any] = {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
        }
    elif kind == "object" or kind is None or any(
            key in schema for key in ("oneOf", "anyOf", "allOf", "$ref")):
        result = {"type": "object", "additionalProperties": True}
    else:
        result = {"type": kind}
        if "enum" in schema:
            result["enum"] = schema["enum"]
    description = schema.get("description")
    if isinstance(description, str) and description and len(description) <= 240:
        result["description"] = description
    return result


def compact_schema(name: str, schema: Any) -> Any:
    """Return the advertised projection for one of ``COMPACT_TOOLS``."""
    if name not in COMPACT_TOOLS or not isinstance(schema, Mapping):
        return schema
    result: dict[str, Any] = {
        "type": "object",
        "properties": {
            key: _summary_property(value)
            for key, value in schema.get("properties", {}).items()
        },
    }
    if "required" in schema:
        result["required"] = schema["required"]
    if "additionalProperties" in schema:
        result["additionalProperties"] = schema["additionalProperties"]
    # Preserve only the top-level alternative requirements; nested details stay
    # authoritative in inputSchema and are served on demand by mcp_help.
    if isinstance(schema.get("oneOf"), list):
        branches = [
            {"required": branch["required"]}
            for branch in schema["oneOf"]
            if isinstance(branch, Mapping) and isinstance(branch.get("required"), list)
        ]
        if branches:
            result["oneOf"] = branches
    return result


def wire_input_schema(definition: Mapping[str, Any]) -> Any:
    """Select the compact or full schema for one definition."""
    name = definition.get("name", "")
    return compact_schema(name, definition.get("inputSchema"))


__all__ = ["COMPACT_TOOLS", "compact_schema", "wire_input_schema"]
