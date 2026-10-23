"""Smallest schema-valid input examples for corrective MCP errors.

The examples are generated from the authoritative input schema and then
checked by the same validator used for live calls.  They are deliberately
minimal: required fields only, smallest valid arrays and strings, and no
optional payload.  Session-derived references still have to be replaced by
the caller; this module never pretends that a synthetic identifier is live
state.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .circuit_spec_schema import CircuitSpecError, validate
from .mcp_regex_examples import regex_example
from .schema_refs import expand

MAX_EXAMPLE_CHARS = 900

_NAME_CANDIDATES = {
    "code": ("nil",),
    "library": ("WORK",),
    "cell": ("CELL",),
    "view": ("schematic",),
    "module": ("top",),
    "name": ("example",),
    "path": ("/tmp/example",),
    "query": ("example",),
    "pattern": ("example",),
    "reference": ("example",),
    "profile": ("amsverify",),
    "through": ("snapshot",),
    "recipe_id": ("recipe-1",),
    "request_id": ("request-1",),
    "task_id": ("task_1",),
    "task_ref": ("task:1",),
    "snapshot_ref": ("snapshot:1",),
    "project_ref": ("project:1",),
    "report_ref": ("report:1",),
    "result_ref": ("result:1",),
    "setup_ref": ("setup:1",),
    "run_ref": ("run:1",),
    "context_ref": ("context:1",),
    "history": ("history:1",),
    "test": ("tran",),
    "corner": ("tt",),
    "analysis": ("ac",),
    "signal": ("/out",),
    "template_ref": ("tpl_" + "a" * 64,),
    "snapshot_id": ("snap_" + "a" * 64,),
    "preview_digest": ("a" * 64,),
    "geometry_plan_digest": ("a" * 64,),
    "approval_digest": ("a" * 64,),
    "source_generation": ("a" * 64,),
    "source": ("module top; endmodule",),
}


def render_example(example: Any, *, max_chars: int = MAX_EXAMPLE_CHARS) -> str | None:
    """Render one compact JSON example, or None when it would be too long."""
    text = json.dumps(example, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return text if len(text) <= max_chars else None


def example_for(tool_name: str, schema: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a minimal object example that passes ``schema``."""
    try:
        expanded = expand(schema)
        example = _generate(expanded)
        if not isinstance(example, dict):
            return None
        validate(example, expanded)
        return example
    except (CircuitSpecError, KeyError, TypeError, ValueError, RecursionError):
        return None


def attach_example(
    result: dict[str, Any],
    tool_name: str | None,
    schema: Mapping[str, Any] | None,
) -> None:
    """Attach one validated input example without replacing a domain example."""
    if tool_name is None or schema is None or "example" in result:
        return
    example = example_for(tool_name, schema)
    if example is None:
        return
    rendered = render_example(example)
    if rendered is None:
        return
    result["example"] = example
    result["example_source"] = "input_schema"
    result["example_note"] = (
        "Schema-valid shape; replace session-derived references with current values."
    )
    result.setdefault("cause", str(result.get("message", "")))
    result["message"] = (
        str(result.get("message", ""))
        + " Correct call example (schema-valid; replace session-derived values): "
        + rendered
    ).strip()


def _merge(parent: Mapping[str, Any], branch: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in parent.items() if key not in {"oneOf", "anyOf"}}
    properties = dict(parent.get("properties", {}))
    properties.update(branch.get("properties", {}))
    if properties:
        result["properties"] = properties
    required = list(parent.get("required", ()))
    for name in branch.get("required", ()):
        if name not in required:
            required.append(name)
    if required:
        result["required"] = required
    for key, value in branch.items():
        if key not in {"properties", "required"}:
            result[key] = value
    return result


def _generate(schema: Any, name: str = "", depth: int = 0) -> Any:
    if depth > 20 or not isinstance(schema, Mapping):
        raise ValueError("schema example depth or shape is unsupported")
    if "const" in schema:
        return schema["const"]
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    if "default" in schema:
        candidate = schema["default"]
        try:
            validate(candidate, schema)
            return candidate
        except CircuitSpecError:
            pass
    for keyword in ("oneOf", "anyOf"):
        if isinstance(schema.get(keyword), list):
            for branch in schema[keyword]:
                try:
                    candidate = _generate(_merge(schema, branch), name, depth + 1)
                    validate(candidate, schema)
                    return candidate
                except (CircuitSpecError, KeyError, TypeError, ValueError, RecursionError):
                    continue
            raise ValueError("no valid " + keyword + " branch")
    allowed = ("properties", "required", "additionalProperties", "minProperties")
    kind = schema.get("type")
    if kind is None and any(key in schema for key in allowed):
        kind = "object"
    if isinstance(kind, list):
        for candidate_kind in kind:
            try:
                return _generate({**schema, "type": candidate_kind}, name, depth + 1)
            except (CircuitSpecError, KeyError, TypeError, ValueError, RecursionError):
                continue
        raise ValueError("no valid union type")
    if kind == "object":
        return _object(schema, depth)
    if kind == "array":
        return _array(schema, name, depth)
    if kind == "string":
        return _string(schema, name)
    if kind in {"integer", "number"}:
        minimum = schema.get("minimum", 0)
        maximum = schema.get("maximum", 1e100)
        return 0 if minimum <= 0 <= maximum else minimum
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    if kind is None:
        return "example"
    raise ValueError("unsupported schema type " + str(kind))


def _object(schema: Mapping[str, Any], depth: int) -> dict[str, Any]:
    properties = schema.get("properties", {})
    value: dict[str, Any] = {}
    for key in schema.get("required", ()):
        child = properties.get(key)
        if child is None:
            child = schema.get("additionalProperties", False)
        if not isinstance(child, Mapping):
            raise ValueError("required field has no example schema: " + str(key))
        value[key] = _generate(child, str(key), depth + 1)
    if not value:
        for key in _optional_example_keys(properties):
            child = properties[key]
            if not isinstance(child, Mapping):
                continue
            candidate = _generate(child, str(key), depth + 1)
            trial = {**value, key: candidate}
            try:
                validate(trial, schema)
            except CircuitSpecError:
                continue
            value = trial
    minimum = schema.get("minProperties", 0)
    for key, child in properties.items():
        if len(value) >= minimum:
            break
        if key not in value and isinstance(child, Mapping):
            value[key] = _generate(child, str(key), depth + 1)
    while len(value) < minimum:
        key = _mapping_key(schema, len(value))
        child = schema.get("additionalProperties", True)
        value[key] = "example" if child is True else _generate(child, key, depth + 1)
    return value


def _optional_example_keys(properties: Mapping[str, Any]) -> list[str]:
    """Choose a few representative optional keys without inventing a call."""
    keys: list[str] = []
    if "action" in properties:
        return ["action"]
    if "mode" in properties:
        keys.append("mode")
    for key, schema in properties.items():
        description = schema.get("description") if isinstance(schema, Mapping) else None
        if isinstance(description, str) and "required" in description.casefold():
            keys.append(key)
    for key, schema in properties.items():
        if isinstance(schema, Mapping) and "default" in schema:
            keys.append(key)
    return list(dict.fromkeys(keys))[:3]


def _mapping_key(schema: Mapping[str, Any], index: int) -> str:
    rule = schema.get("propertyNames")
    if isinstance(rule, Mapping):
        try:
            key = _generate(rule, "key", 1)
            if isinstance(key, str) and key and key not in schema.get("properties", {}):
                return key
        except (CircuitSpecError, KeyError, TypeError, ValueError, RecursionError):
            pass
    return "key" + str(index)


def _array(schema: Mapping[str, Any], name: str, depth: int) -> list[Any]:
    count = schema.get("minItems", 0)
    if count > 8:
        raise ValueError("array minimum is too large for a short example")
    values = [_generate(schema["items"], name, depth + 1) for _ in range(count)]
    unique = {json.dumps(value, sort_keys=True) for value in values}
    if schema.get("uniqueItems") and len(unique) != len(values):
        raise ValueError("unique array example would contain duplicates")
    return values


def _string(schema: Mapping[str, Any], name: str) -> str:
    candidates = list(_NAME_CANDIDATES.get(name, ()))
    if any(word in name.casefold() for word in ("sha", "digest", "hash")):
        candidates.append("a" * 64)
    if "example" in schema:
        candidates.insert(0, schema["example"])
    if "default" in schema and isinstance(schema["default"], str):
        candidates.insert(0, schema["default"])
    pattern = schema.get("pattern")
    if pattern:
        candidates.append(regex_example(pattern))
    candidates.append("example")
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        value = candidate
        minimum = schema.get("minLength", 0)
        maximum = schema.get("maxLength", 256)
        if len(value) < minimum:
            value += "x" * (minimum - len(value))
        if len(value) > maximum:
            value = value[:maximum]
        if pattern and not re.fullmatch(pattern, value):
            continue
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            continue
        return value
    raise ValueError("no valid string example")


__all__ = ["MAX_EXAMPLE_CHARS", "attach_example", "example_for", "render_example"]
