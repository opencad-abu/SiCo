"""Tool-level help views and the shared read helpers the help modules use.

Every view reads the injected catalog; nothing keeps a second copy of a
description, schema or parameter rule.  Availability stays an injected fact
(default: the catalogued tool is available on this server).  Bounded answers
carry explicit ``complete``/``truncated`` markers; a truncated schema is never
returned as if it were complete.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Optional

from .mcp_definitions import ALIASES
from .project_schema import OPERATIONS, operation_tool
from .schema_refs import SchemaRefError, expand
from .socket_server import RequestFailure

Catalog = Mapping[str, Mapping[str, Any]]
Availability = Callable[[str], Optional[Mapping[str, Any]]]

LIST_LIMIT = 20
LIST_BYTES = 8192
DESCRIBE_BYTES = 4096
SCHEMA_BYTES = 8192
PURPOSE_CHARS = 160
DESCRIPTION_CHARS = 1200

# Operations whose same-named tool is registered only to preserve its input
# schema. Their writes are journal-only and are deliberately rejected by the
# direct handler; advertising ``direct_tool`` here would point the model at a
# path that cannot execute.
DIRECT_CALL_DISABLED = frozenset({"apply_cdf_update", "create_circuit_library"})

_POINTER_TOKEN = re.compile(r"^[^/~]*$")


def compact(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(definition: Mapping[str, Any]) -> str:
    return hashlib.sha256(compact(definition)).hexdigest()


def purpose(definition: Mapping[str, Any], limit: int = PURPOSE_CHARS) -> str:
    text = " ".join(str(definition.get("description", "")).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def availability(name: str, resolve: Availability | None) -> dict[str, Any]:
    record = resolve(name) if resolve is not None else None
    if record is None:
        return {"state": "available"}
    state = record.get("state")
    if state not in {"available", "requires_profile", "unavailable"}:
        raise RequestFailure("help_unavailable", f"invalid availability for {name}")
    return {"state": state, **{key: value for key, value in record.items() if key != "state"}}


def canonical(catalog_rows: Catalog, name: str) -> tuple[Mapping[str, Any], str | None]:
    """Resolve one name to its canonical definition; aliases stay visible."""
    row = catalog_rows.get(name)
    if row is not None:
        return row, None
    target = ALIASES.get(name)
    row = catalog_rows.get(target) if target else None
    if row is None:
        raise RequestFailure("unknown_tool", f"no such tool: {name}")
    return row, name


def dispatch_entry(name: str, catalog_rows: Catalog) -> dict[str, Any]:
    """The authoritative execution entry for one canonical tool name.

    Operations are journalled through ``execute_circuit_operation``; a
    same-named direct tool stays available and is named when it exists.
    """
    if name in OPERATIONS:
        entry = {"tool": "execute_circuit_operation", "operation": name}
        direct = operation_tool(name)
        if direct in catalog_rows and name not in DIRECT_CALL_DISABLED:
            entry["direct_tool"] = direct
        return entry
    return {"tool": name}


def resolve_pointer(schema: Any, pointer: str) -> Any:
    if pointer in ("", None):
        return schema
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise RequestFailure("invalid_params",
                             "schema_path must be '' or a JSON Pointer starting with '/'")
    node = schema
    for token in pointer[1:].split("/"):
        if not _POINTER_TOKEN.match(token):
            raise RequestFailure("invalid_params", f"invalid JSON Pointer token: {token!r}")
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, Mapping):
            if token not in node:
                raise RequestFailure("schema_path_not_found", f"no such schema_path: {pointer}")
            node = node[token]
        elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
            if not token.isdigit() or int(token) >= len(node):
                raise RequestFailure("schema_path_not_found", f"no such schema_path: {pointer}")
            node = node[int(token)]
        else:
            raise RequestFailure("schema_path_not_found", f"no such schema_path: {pointer}")
    return node


# 被截断的 schema 片段给出下一层可读路径与各自体积：智能体一次就能挑准
# schema_path，而不是逐路径试探（真实记录：单任务 31 次 mcp_help 调用）。
EXPANDED_KEYS = ("properties", "$defs", "definitions")
LIST_KEYS = ("oneOf", "anyOf", "allOf")
CHILDREN_LIMIT = 24


def pointer_children(schema: Any, pointer: str, limit: int = CHILDREN_LIMIT) -> list[dict[str, Any]]:
    if not isinstance(schema, Mapping):
        return []
    base = pointer if pointer not in (None, "") else ""
    rows: list[dict[str, Any]] = []

    def add(path: str, value: Any) -> None:
        rows.append({"path": path, "bytes": len(compact(value))})

    for key, value in list(schema.items()):
        path = f"{base}/{key}"
        if key in EXPANDED_KEYS and isinstance(value, Mapping):
            for child_key, child in list(value.items()):
                add(f"{path}/{child_key}", child)
                if len(rows) >= limit:
                    return rows
        elif key in LIST_KEYS and isinstance(value, list):
            for index, child in enumerate(value):
                add(f"{path}/{index}", child)
                if len(rows) >= limit:
                    return rows
        else:
            add(path, value)
        if len(rows) >= limit:
            break
    return rows


def bounded(result: dict[str, Any], budget: int, rows_key: str | None) -> dict[str, Any]:
    """Keep an answer inside its budget; if rows must drop, say so explicitly."""
    while len(compact(result)) > budget and rows_key and result.get(rows_key):
        result[rows_key].pop()
        result["truncated"] = True
        result["complete"] = False
    if len(compact(result)) > budget:
        raise RequestFailure("help_output_limit", "response exceeds the help budget")
    return result


def list_tools(rows_by_name: Catalog, query: str | None, offset: int, limit: int,
               availability_resolver: Availability | None) -> dict[str, Any]:
    terms = [term.lower() for term in (query or "").split() if term]
    rows = []
    for row in rows_by_name.values():
        haystack = " ".join(
            str(row.get(key, "")) for key in ("name", "title", "description")).lower()
        if all(term in haystack for term in terms):
            record = availability(row["name"], availability_resolver)
            entry = {"name": row["name"], "purpose": purpose(row),
                     "availability": record["state"]}
            if record["state"] != "available" and record.get("reason"):
                entry["reason"] = str(record["reason"])[:160]
            for key in ("profile", "required_profile"):
                if record.get(key):
                    entry[key] = record[key]
            rows.append(entry)
    window = rows[offset: offset + limit]
    result = {
        "ok": True, "action": "list", "entity": "tools", "scope": "global",
        "total": len(rows), "offset": offset, "count": len(window),
        "complete": offset + len(window) >= len(rows),
        "truncated": offset + len(window) < len(rows),
        "tools": window,
    }
    if result["truncated"]:
        result["next_offset"] = offset + len(window)
    return bounded(result, LIST_BYTES, "tools")


def describe_tool(definition: Mapping[str, Any], legacy: str | None, name: str,
                  record: Mapping[str, Any], catalog_rows: Catalog,
                  help_note: str | None = None,
                  session: Mapping[str, Any] | None = None) -> dict[str, Any]:
    description = " ".join(str(definition.get("description", "")).split())
    schema = definition.get("inputSchema")
    properties = list((schema or {}).get("properties", {}))
    result: dict[str, Any] = {
        "ok": True, "action": "describe", "complete": True,
        "tool": {
            "name": name,
            "purpose": description[:DESCRIPTION_CHARS],
            "availability": record,
            "dispatch": dispatch_entry(name, catalog_rows),
            "schema_summary": {
                "properties": properties,
                "required": list((schema or {}).get("required", [])),
                "additional_properties": (schema or {}).get("additionalProperties"),
            },
            "digest": "sha256:" + digest(definition),
        },
    }
    if legacy:
        result["tool"]["legacy_name"] = legacy
    if session:
        result["tool"]["session_profile"] = session.get("profile")
        result["tool"]["active_workflow"] = session.get("active_workflow")
        result["tool"]["allowed_workflows"] = list(
            session.get("allowed_workflows", ()))
        if name == "execute_circuit_operation":
            result["tool"]["allowed_operations"] = list(
                session.get("operations", ()))
    from . import mcp_examples

    if name in mcp_examples.EXAMPLES:
        result["tool"]["example"] = {"action": "example", "tool": name}
    full_help = help_note if help_note else None
    if full_help:
        # On-demand detail owned by the declaring module; never advertised.
        result["tool"]["help"] = full_help
    properties = result["tool"]["schema_summary"]["properties"]
    while len(compact(result)) > DESCRIBE_BYTES and properties:
        properties.pop()
        result["tool"]["schema_summary"]["properties_truncated"] = True
        result["complete"] = False
    if full_help and len(compact(result)) > DESCRIBE_BYTES:
        low, high, best = 0, len(full_help), 0
        while low <= high:
            middle = (low + high) // 2
            result["tool"]["help"] = full_help[:middle]
            result["tool"]["help_truncated"] = True
            result["complete"] = False
            if len(compact(result)) <= DESCRIBE_BYTES:
                best, low = middle, middle + 1
            else:
                high = middle - 1
        result["tool"]["help"] = full_help[:best]
        if best == 0:
            del result["tool"]["help"]
    return bounded(result, DESCRIBE_BYTES, None)


def schema_view(definition: Mapping[str, Any], legacy: str | None, name: str,
                schema_path: str | None, catalog_rows: Catalog,
                budget: int = SCHEMA_BYTES) -> dict[str, Any]:
    schema = definition.get("inputSchema")
    if not isinstance(schema, Mapping):
        raise RequestFailure("schema_unavailable", f"{name} has no inputSchema")
    fragment = resolve_pointer(schema, schema_path)
    try:
        # A served fragment is self-contained: local $defs references expand
        # against the same authoritative schema before the budget check.
        fragment = expand(fragment, root=schema)
    except SchemaRefError as exc:
        raise RequestFailure("schema_ref_unresolved", f"{name}: {exc}") from exc
    encoded = compact(fragment)
    result: dict[str, Any] = {
        "ok": True, "action": "schema", "tool": name,
        "dispatch": dispatch_entry(name, catalog_rows),
        "schema_path": schema_path or "", "complete": len(encoded) <= budget,
        "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest(),
    }
    if legacy:
        result["legacy_name"] = legacy
    if result["complete"]:
        result["schema"] = fragment
    else:
        result.update({
            "truncated": True,
            "message": "fragment exceeds the help budget; this is NOT a complete "
                       "schema. children lists the exact readable schema_path values "
                       "and their sizes: read the narrow fragments you need.",
        })
        children = pointer_children(fragment, schema_path)
        result["children"] = children
        if len(children) >= CHILDREN_LIMIT:
            result["children_truncated"] = True
    return bounded(result, budget, None)


__all__ = [
    "Availability", "Catalog", "DESCRIBE_BYTES", "DESCRIPTION_CHARS", "LIST_BYTES",
    "LIST_LIMIT", "PURPOSE_CHARS", "SCHEMA_BYTES", "CHILDREN_LIMIT",
    "availability", "bounded", "canonical", "compact", "describe_tool", "digest",
    "dispatch_entry", "list_tools", "pointer_children", "purpose", "resolve_pointer",
    "schema_view",
]
