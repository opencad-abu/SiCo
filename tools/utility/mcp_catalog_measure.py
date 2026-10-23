"""Deterministic byte measurement primitives for the SiCo MCP catalog report.

Frozen measurement contract (version ``sico-mcp-bytes.v1``):

- UTF-8 JSON via ``json.dumps(ensure_ascii=False, allow_nan=False)``;
  the compact form additionally sorts keys and uses ``(",", ":")`` separators.
- Per-item sums and single-array bytes are reported separately and never mixed.
- ``sha256`` is over the compact array bytes of one layer.
- Schema annotation bytes are schema bytes minus structure-only bytes, where
  ``description``/``title``/``examples``/``$comment`` are dropped at any depth.
- Every layer is measured independently; the same tool may appear in several
  layers (canonical definitions, canonical MCP fields, host projection) and is
  counted once per layer.

This module measures dictionaries only.  It imports no EDA code and calls no
model, service or Virtuoso process.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

METHOD = "sico-mcp-bytes.v1"

# The four fields one MCP tool row carries on the wire.
WIRE_FIELDS = ("name", "description", "inputSchema", "annotations")

# Keys inside an inputSchema that explain rather than constrain.
ANNOTATION_KEYS = frozenset({"description", "title", "examples", "$comment"})

_SCHEMA_MAPS = ("properties", "patternProperties", "$defs", "definitions")
_SCHEMA_SINGLE = ("items", "additionalProperties", "propertyNames", "not",
                  "if", "then", "else", "contains")
_SCHEMA_LISTS = ("oneOf", "anyOf", "allOf", "prefixItems")


def encoded(value: Any, *, compact: bool = False) -> bytes:
    """Encode one JSON value under the frozen measurement contract."""
    options: dict[str, Any] = {"ensure_ascii": False, "allow_nan": False}
    if compact:
        options.update(sort_keys=True, separators=(",", ":"))
    return json.dumps(value, **options).encode("utf-8")


def wire_row(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Project one registered definition onto the four MCP wire fields."""
    missing = [field for field in WIRE_FIELDS if field not in definition]
    if missing:
        raise ValueError("definition lacks MCP fields: " + ", ".join(missing))
    return {field: definition[field] for field in WIRE_FIELDS}


def structural_schema(schema: Any) -> Any:
    """Drop explanatory schema keys, keeping the constraining structure."""
    if not isinstance(schema, dict):
        return schema
    result = {key: value for key, value in schema.items() if key not in ANNOTATION_KEYS}
    for key in _SCHEMA_MAPS:
        if key in result:
            result[key] = {name: structural_schema(value)
                           for name, value in result[key].items()}
    for key in _SCHEMA_SINGLE:
        if key in result:
            result[key] = structural_schema(result[key])
    for key in _SCHEMA_LISTS:
        if key in result:
            result[key] = [structural_schema(value) for value in result[key]]
    return result


def tool_bytes(row: Mapping[str, Any]) -> dict[str, Any]:
    """One tool's contribution: bytes, hash and schema split."""
    default = encoded(row)
    compact = encoded(row, compact=True)
    name = row.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("measured MCP rows require a name")
    record: dict[str, Any] = {
        "name": name,
        "default_bytes": len(default),
        "compact_bytes": len(compact),
        "sha256": hashlib.sha256(compact).hexdigest(),
    }
    content = {key: value for key, value in row.items() if key != "name"}
    record["content_sha256"] = hashlib.sha256(
        encoded(content, compact=True)).hexdigest()
    description = row.get("description")
    record["description_bytes"] = len(encoded(description)) if isinstance(description, str) else 0
    schema = row.get("inputSchema")
    if isinstance(schema, dict):
        structural_row = structural_schema(schema)
        record["structure_sha256"] = hashlib.sha256(
            encoded(structural_row, compact=True)).hexdigest()
        structural = len(encoded(structural_row))
        record["schema_bytes"] = len(encoded(schema))
        record["annotation_bytes"] = record["schema_bytes"] - structural
        record["structural_bytes"] = structural
    return record


def measurement(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Measure one layer and keep per-tool records for later diffs.

    Row order is the layer's wire order and is preserved in the array bytes;
    per-item sums and field bytes are order-independent.  The per-tool list is
    sorted by name so diffs and reports stay stable.
    """
    tools = [tool_bytes(row) for row in rows]
    tools.sort(key=lambda row: row["name"])
    fields = sorted({key for row in rows for key in row})
    return {
        "method": METHOD,
        "count": len(rows),
        "sum_default_bytes": sum(len(encoded(row)) for row in rows),
        "sum_compact_bytes": sum(len(encoded(row, compact=True)) for row in rows),
        "array_default_bytes": len(encoded(list(rows))),
        "array_compact_bytes": len(encoded(list(rows), compact=True)),
        "sha256": hashlib.sha256(
            encoded(list(rows), compact=True)).hexdigest(),
        "field_bytes": {
            key: sum(len(encoded(row[key])) for row in rows if key in row)
            for key in fields
        },
        "schema_annotation_bytes": sum(
            row.get("annotation_bytes", 0) for row in tools),
        "tools": tools,
        "largest": [
            {"name": row["name"], "default_bytes": row["default_bytes"]}
            for row in sorted(tools, key=lambda r: (-r["default_bytes"], r["name"]))[:10]
        ],
    }


def compare(old_tools: Iterable[Mapping[str, Any]],
            new_tools: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Deterministic layer diff: added, removed, renamed and changed names."""
    old = {row["name"]: row for row in old_tools}
    new = {row["name"]: row for row in new_tools}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))

    renamed: list[dict[str, str]] = []
    pending = {name: new[name]["content_sha256"] for name in added}
    for name in list(removed):
        digest = old[name]["content_sha256"]
        targets = sorted(candidate for candidate, value in pending.items()
                         if value == digest)
        sources = [candidate for candidate in removed
                   if old[candidate]["content_sha256"] == digest]
        if len(targets) == 1 and len(sources) == 1:
            target = targets[0]
            renamed.append({"from": name, "to": target})
            removed.remove(name)
            added.remove(target)
            pending.pop(target)
    changed = sorted(
        name for name in set(old) & set(new)
        if old[name]["sha256"] != new[name]["sha256"]
    )
    return {
        "added": added,
        "removed": removed,
        "renamed": renamed,
        "changed": changed,
        "unchanged_count": len(set(old) & set(new)) - len(changed),
    }


__all__ = [
    "METHOD",
    "WIRE_FIELDS",
    "ANNOTATION_KEYS",
    "encoded",
    "wire_row",
    "structural_schema",
    "tool_bytes",
    "measurement",
    "compare",
]
