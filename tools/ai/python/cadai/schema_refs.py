"""The local ``$defs`` reference mechanism: factoring a schema, expanding it back.

An authored tool schema may factor a repeated structure into a root ``$defs``
section and reference it as ``{"$ref": "#/$defs/<name>"}``.  References are
local to a single tool schema: they never cross tools and never address the
network.  ``expand`` is the one authoritative expansion used by server-side
validation and by the on-demand schema views; it refuses unknown, non-local or
circular references instead of guessing.  ``factor`` derives the factored
projection of an explicitly authored schema and keeps only the definitions it
actually references, so the explicit schema stays the single authored source.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

PREFIX = "#/$defs/"

# Rough compact-byte costs of a reference node and its definitions entry; a
# repeated subtree must be larger than these plus the entry to pay for itself.
REF_BYTES = 32
DEF_BYTES = 44


class SchemaRefError(ValueError):
    """An authored schema contains an unresolvable local reference."""


def _target(root: Any, ref: Any) -> tuple[Any, str]:
    if not isinstance(ref, str) or not ref.startswith(PREFIX):
        raise SchemaRefError("reference must be a local $defs pointer: " + repr(ref))
    name = ref[len(PREFIX):]
    if not name or "/" in name or "~" in name:
        raise SchemaRefError("invalid $defs name: " + repr(name))
    definitions = root.get("$defs") if isinstance(root, dict) else None
    if not isinstance(definitions, dict) or name not in definitions:
        raise SchemaRefError("unknown $defs reference: " + name)
    return definitions[name], name


def expand(schema: Any, root: Any = None, _stack: tuple[str, ...] = ()) -> Any:
    """Return a copy of ``schema`` with every local ``$ref`` replaced in place.

    ``root`` supplies the ``$defs`` a fragment points into; it defaults to the
    schema itself.  A ``$ref`` node carries the reference alone: sibling
    keywords next to it are refused rather than silently ignored.
    """
    if root is None:
        root = schema
    if isinstance(schema, dict):
        if "$ref" in schema:
            if len(schema) != 1:
                raise SchemaRefError("$ref cannot carry sibling keywords")
            target, name = _target(root, schema["$ref"])
            if name in _stack:
                raise SchemaRefError(
                    "circular $defs reference: " + " -> ".join(_stack + (name,)))
            return expand(target, root=root, _stack=_stack + (name,))
        return {
            key: expand(value, root=root, _stack=_stack)
            for key, value in schema.items() if key != "$defs"
        }
    if isinstance(schema, list):
        return [expand(value, root=root, _stack=_stack) for value in schema]
    return schema


def _size(node: Any) -> int:
    return len(json.dumps(node, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _refs_in(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(PREFIX):
            found.add(ref[len(PREFIX):])
        for value in node.values():
            _refs_in(value, found)
    elif isinstance(node, list):
        for value in node:
            _refs_in(value, found)


def factor(schema: Any, defs: Any) -> tuple[Any, dict[str, Any]]:
    """Return the factored projection of ``schema`` and the definitions it uses.

    A subtree is replaced by a reference when it equals the expansion of one
    declared definition, appears at least twice and is large enough to pay for
    the definition entry.  Definition targets may reference each other; the
    returned definitions are closed over those references, so the projection
    expands back to ``schema`` exactly (``expand(factored, root) == schema``).
    When several targets match one subtree, the largest wins, so overlapping
    declarations stay order-independent.
    """
    if not isinstance(defs, dict) or not defs:
        raise SchemaRefError("factor requires a nonempty $defs declaration")
    root = {"$defs": defs}
    targets = [(name, expand(value, root=root)) for name, value in defs.items()]
    targets.sort(key=lambda row: -_size(row[1]))

    def match(node: Any):
        for name, target in targets:
            if node == target:
                return name
        return None

    def count(node: Any, tally: dict[str, int]) -> None:
        if isinstance(node, dict):
            name = match(node)
            if name is not None:
                tally[name] = tally.get(name, 0) + 1
            for value in node.values():
                count(value, tally)
        elif isinstance(node, list):
            for value in node:
                count(value, tally)

    tally: dict[str, int] = {}
    count(schema, tally)
    sizes = {name: _size(target) for name, target in targets}
    repeated = {name for name, seen in tally.items()
                if (seen - 1) * sizes[name] > DEF_BYTES + REF_BYTES * seen}

    def rewrite(node: Any) -> Any:
        if isinstance(node, dict):
            name = match(node)
            if name is not None and name in repeated:
                return {"$ref": PREFIX + name}
            return {key: rewrite(value) for key, value in node.items()}
        if isinstance(node, list):
            return [rewrite(value) for value in node]
        return node

    factored = rewrite(schema)
    used: set[str] = set()
    _refs_in(factored, used)
    pending = set(used)
    while pending:
        name = pending.pop()
        if name not in defs:
            raise SchemaRefError("unknown $defs reference: " + name)
        nested: set[str] = set()
        _refs_in(defs[name], nested)
        pending |= nested - used
        used |= nested
    return factored, {name: defs[name] for name in defs if name in used}


def auto_factor(schema: Any, *, min_bytes: int = 32, max_defs: int = 64) -> tuple[Any, dict[str, Any]]:
    """Derive a local ``$defs`` projection from repeated authored subtrees.

    The authored schema stays the single source: the definitions are collected
    mechanically, ``factor`` decides which repeated subtrees pay for a
    reference, and the result is expanded back before it is returned. Existing
    hand-reviewed definitions are retained and new auto definitions are added
    beside them, so their stable names and review history are preserved.
    """
    if not isinstance(schema, dict):
        return schema, {}
    existing = schema.get("$defs") or {}
    if not isinstance(existing, dict):
        raise SchemaRefError("$defs must be an object")

    found: dict[str, tuple[Any, int]] = {}

    def size(node: Any) -> int:
        return len(json.dumps(node, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node:
                key = json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                previous = found.get(key)
                found[key] = (node, (previous[1] if previous else 0) + 1)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            if node:
                key = json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                previous = found.get(key)
                found[key] = (node, (previous[1] if previous else 0) + 1)
            for value in node:
                walk(value)

    walk(schema)
    candidates = [
        (size(node), key, node)
        for key, (node, count) in found.items()
        if count >= 2 and node is not schema and size(node) >= min_bytes
    ]
    candidates.sort(key=lambda row: (-row[0], row[1]))
    defs = deepcopy(existing)
    index = 0
    for _, _, node in candidates[:max_defs]:
        name = "d%03d" % index
        while name in defs:
            index += 1
            name = "d%03d" % index
        defs[name] = deepcopy(node)
        index += 1
    if not defs:
        return schema, {}
    factored, used = factor(schema, defs)
    if not used:
        return schema, {}
    result = deepcopy(factored)
    result["$defs"] = used
    if expand(result) != expand(schema):
        raise SchemaRefError("auto-factored projection does not expand to the authored schema")
    return result, used
