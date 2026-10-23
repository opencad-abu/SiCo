"""Declaration contract and validation for task groups.

A task group declares which tools cooperate on one task: stable id, owner,
acceptance status, purpose, search keywords, tool references (a name or an
entry/operation pair), subgroup references, conditional extensions, recovery
members and short hand-off notes.  Declarations are owned by their domain and
merged here into one validated map; closure, availability and digests live in
``task_group_resolver``.  Validation is offline: it never executes a tool.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_FIELDS = frozenset({"id", "owner", "status", "purpose", "keywords", "tools",
                     "subgroups", "conditions", "recovery", "connections"})
_STATUS = frozenset({"draft", "reviewed"})


class GroupDeclarationError(ValueError):
    """A declaration that cannot be resolved safely."""


def member_name(member: Any, group_id: str) -> str:
    """The tool name of one member reference."""
    if isinstance(member, str) and member:
        return member
    if isinstance(member, Mapping) and set(member) == {"tool", "operation"}:
        name, operation = member["tool"], member["operation"]
        if isinstance(name, str) and name and isinstance(operation, str) and operation:
            return name
    raise GroupDeclarationError(f"{group_id}: invalid member reference {member!r}")


def member_key(member: Any) -> tuple[str, str | None]:
    if isinstance(member, str):
        return member, None
    return member["tool"], member["operation"]


def _members(value: Any, group_id: str, field: str) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        raise GroupDeclarationError(f"{group_id}: {field} must be a list")
    for member in value:
        member_name(member, group_id)
    return list(value)


def validate(declarations: Mapping[str, Mapping[str, Any]]
             | Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Validate one merged declaration set; returns it indexed by id."""
    rows = declarations.values() if isinstance(declarations, Mapping) else declarations
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise GroupDeclarationError("each group declaration must be a mapping")
        unknown = set(row) - _FIELDS
        if unknown:
            raise GroupDeclarationError(
                f"{row.get('id')}: unknown fields " + ", ".join(sorted(unknown)))
        group_id = row.get("id")
        if not isinstance(group_id, str) or not _ID_PATTERN.match(group_id):
            raise GroupDeclarationError(f"invalid group id: {group_id!r}")
        if group_id in indexed:
            raise GroupDeclarationError(f"duplicate group id: {group_id}")
        for field in ("owner", "purpose"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise GroupDeclarationError(f"{group_id}: {field} is required")
        if row.get("status") not in _STATUS:
            raise GroupDeclarationError(f"{group_id}: status must be draft or reviewed")
        if not all(isinstance(word, str) and word.strip()
                   for word in row.get("keywords", [])):
            raise GroupDeclarationError(f"{group_id}: keywords must be non-empty strings")
        _members(row.get("tools", []), group_id, "tools")
        _members(row.get("recovery", []), group_id, "recovery")
        if not all(isinstance(note, str) and note.strip()
                   for note in row.get("connections", [])):
            raise GroupDeclarationError(f"{group_id}: connections must be strings")
        for condition in row.get("conditions", []):
            if (not isinstance(condition, Mapping)
                    or not isinstance(condition.get("when"), str)
                    or not condition["when"].strip()):
                raise GroupDeclarationError(f"{group_id}: condition requires 'when'")
            _members(condition.get("tools"), group_id, "condition tools")
            if not condition["tools"]:
                raise GroupDeclarationError(f"{group_id}: condition tools cannot be empty")
        indexed[group_id] = dict(row)
    for group_id, row in indexed.items():
        for reference in row.get("subgroups", []):
            if reference == group_id:
                raise GroupDeclarationError(f"{group_id}: group references itself")
            if reference not in indexed:
                raise GroupDeclarationError(
                    f"{group_id}: unknown subgroup {reference!r}")
            if indexed[reference].get("subgroups"):
                raise GroupDeclarationError(
                    f"{group_id}: composition nests beyond one level at {reference}")
    _reject_cycles(indexed)
    return indexed


def _reject_cycles(indexed: Mapping[str, Mapping[str, Any]]) -> None:
    visiting: set[str] = set()
    done: set[str] = set()

    def walk(group_id: str) -> None:
        if group_id in done:
            return
        if group_id in visiting:
            raise GroupDeclarationError(f"subgroup cycle at {group_id}")
        visiting.add(group_id)
        for reference in indexed[group_id].get("subgroups", []):
            walk(reference)
        visiting.discard(group_id)
        done.add(group_id)

    for group_id in indexed:
        walk(group_id)


def merge(*declaration_sets: Mapping[str, Mapping[str, Any]]
          | Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Merge owner sets into one declaration map; duplicate ids are errors."""
    rows: list[Mapping[str, Any]] = []
    for declarations in declaration_sets:
        rows.extend(declarations.values() if isinstance(declarations, Mapping) else declarations)
    return validate(rows)


__all__ = [
    "GroupDeclarationError", "member_key", "member_name", "merge", "validate",
]
