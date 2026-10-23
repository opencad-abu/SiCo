"""Closure, availability and digests for validated task-group declarations.

The declaration contract lives in ``task_group_schema``; this module resolves
one group against one injected catalog: deduplicated members in declaration
order, conditional extensions kept separate, recovery members that must stay
reachable, and missing members reported with their reason and availability.
Nothing here executes a tool, reads EDA state or changes query scope.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .task_group_schema import GroupDeclarationError, member_key, member_name


def _operation_enum(row: Mapping[str, Any]) -> list[str] | None:
    schema = row.get("inputSchema")
    operation = (schema or {}).get("properties", {}).get("operation", {})
    values = operation.get("enum")
    if not isinstance(values, list):
        return None
    return [value for value in values if isinstance(value, str)]


def _resolve_members(group_id: str, members: Sequence[Any],
                     catalog: Mapping[str, Mapping[str, Any]],
                     availability=None) -> tuple[list[tuple[str, str | None]],
                                                 list[dict[str, Any]]]:
    """Resolve references in declaration order; report missing entries with reasons."""
    resolved: list[tuple[str, str | None]] = []
    missing: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for member in members:
        name, operation = member_key(member)
        key = (name, operation)
        if key in seen:
            continue
        seen.add(key)
        row = catalog.get(name)
        if row is None:
            missing.append({
                "name": name,
                "operation": operation,
                "reason": "该宿主未注册此工具" if availability is None else
                          "宿主未注册或当前目录不可用",
                "state": (availability(name) or {}).get("state", "unavailable")
                if availability is not None else "unavailable",
            })
            continue
        if operation is not None:
            allowed = _operation_enum(row)
            if allowed is None:
                missing.append({"name": name, "operation": operation,
                                "reason": "该工具没有 operation 枚举", "state": "unavailable"})
                continue
            if operation not in allowed:
                missing.append({"name": name, "operation": operation,
                                "reason": "operation 不在该工具 schema 内",
                                "state": "unavailable"})
                continue
        resolved.append((name, operation))
    return resolved, missing


def _names(pairs: Sequence[tuple[str, str | None]]) -> list[str]:
    """Deduplicated tool names in first-seen order."""
    return list(dict.fromkeys(name for name, _ in pairs))


def _routes(pairs: Sequence[tuple[str, str | None]]) -> list[dict[str, Any]]:
    return [{"tool": name, **({"operation": operation} if operation else {})}
            for name, operation in pairs]


def _member_bytes(names: Sequence[str], catalog: Mapping[str, Mapping[str, Any]]) -> int:
    total = 0
    for name in names:
        row = catalog.get(name)
        if row is not None:
            total += len(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8"))
    return total


def _digest(declaration: Mapping[str, Any], names: Sequence[str],
            catalog: Mapping[str, Mapping[str, Any]]) -> str:
    payload = {"group": declaration,
               "members": {name: catalog.get(name) for name in names}}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def closure_names(declaration: Mapping[str, Any],
                  declarations: Mapping[str, Mapping[str, Any]]) -> list[Any]:
    """Declared members plus subgroup members, in declaration order."""
    members = list(declaration.get("tools", []))
    for reference in declaration.get("subgroups", []):
        members.extend(declarations[reference].get("tools", []))
    return members


def resolve(group_id: str, *, declarations: Mapping[str, Mapping[str, Any]],
            catalog: Mapping[str, Mapping[str, Any]], availability=None) -> dict[str, Any]:
    """One group's required closure, conditional extensions and health."""
    declaration = declarations[group_id]
    member_pairs, missing = _resolve_members(
        group_id, closure_names(declaration, declarations), catalog, availability)
    members = _names(member_pairs)
    conditional: list[str] = []
    conditional_missing: list[dict[str, Any]] = []
    conditions = []
    for condition in declaration.get("conditions", []):
        pairs, absent = _resolve_members(group_id, condition["tools"], catalog, availability)
        names = _names(pairs)
        conditional.extend(name for name in names if name not in conditional)
        conditional_missing.extend(absent)
        conditions.append({"when": condition["when"], "tools": names,
                           "routes": _routes(pairs), "from": group_id})
    for reference in declaration.get("subgroups", []):
        for condition in declarations[reference].get("conditions", []):
            pairs, absent = _resolve_members(group_id, condition["tools"], catalog,
                                             availability)
            names = _names(pairs)
            conditional.extend(name for name in names if name not in conditional)
            conditional_missing.extend(absent)
            conditions.append({"when": condition["when"], "tools": names,
                               "routes": _routes(pairs), "from": reference})
    declared_names = {member_name(member, group_id)
                      for member in closure_names(declaration, declarations)}
    declared_names.update(
        member_name(member, group_id)
        for condition in declaration.get("conditions", [])
        for member in condition["tools"])
    for reference in declaration.get("subgroups", []):
        declared_names.update(
            member_name(member, group_id)
            for condition in declarations[reference].get("conditions", [])
            for member in condition["tools"])
    for member in declaration.get("recovery", []):
        if member_name(member, group_id) not in declared_names:
            raise GroupDeclarationError(
                f"{group_id}: recovery members must be part of the group: "
                + member_name(member, group_id))
    recovery_pairs, recovery_missing = _resolve_members(
        group_id, declaration.get("recovery", []), catalog, availability)
    recovery = _names(recovery_pairs)
    for reference in declaration.get("subgroups", []):
        nested, nested_missing = _resolve_members(
            group_id, declarations[reference].get("recovery", []), catalog, availability)
        recovery.extend(name for name in _names(nested) if name not in recovery)
        recovery_missing.extend(nested_missing)
    outside = [name for name in recovery if name not in members and name not in conditional]
    if outside:
        raise GroupDeclarationError(
            f"{group_id}: recovery members must be part of the group: " + ", ".join(outside))
    return {
        "id": group_id,
        "owner": declaration["owner"],
        "status": declaration["status"],
        "purpose": declaration["purpose"],
        "keywords": list(declaration.get("keywords", [])),
        "subgroups": list(declaration.get("subgroups", [])),
        "tools": members,
        "conditional": conditional,
        "conditions": conditions,
        "recovery": recovery,
        "recovery_missing": recovery_missing,
        "missing": missing,
        "conditional_missing": conditional_missing,
        "connections": list(declaration.get("connections", [])),
        "tool_count": len(members),
        "conditional_count": len(conditional),
        "bytes": _member_bytes(members, catalog),
        "digest": _digest(declaration, members, catalog),
        "available": not missing and not recovery_missing,
    }


__all__ = [
    "closure_names", "resolve",
]
