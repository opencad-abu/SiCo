"""Declaration contract for tool profiles: one session's capability scope.

A profile names what one session may see.  ``scope=declared`` lists explicit
tool names and references task groups whose members (required, conditional and
recovery) join the same scope; ``scope=host`` means every host-supported
registered tool (the migration-time compat surface).  ``defaults`` names the
task groups a session starts with for ranking and query scope; it never
narrows the member set, so switching the default group cannot remove a
capability.  Declarations are owned by their domain and merged here into one
validated map; eligibility resolution lives in ``tool_exposure``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_FIELDS = frozenset({"id", "owner", "status", "purpose", "scope", "tools",
                     "groups", "defaults"})
_STATUS = frozenset({"draft", "reviewed"})
_SCOPES = frozenset({"host", "declared"})


class ProfileDeclarationError(ValueError):
    """A declaration that cannot be resolved safely."""


def _name_list(value: Any, profile_id: str, field: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ProfileDeclarationError(f"{profile_id}: {field} must be a list")
    names: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry:
            raise ProfileDeclarationError(
                f"{profile_id}: {field} entries must be non-empty tool names")
        names.append(entry)
    return names


def validate(declarations: Mapping[str, Mapping[str, Any]]
             | Iterable[Mapping[str, Any]], *,
             groups: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate one merged declaration set; returns it indexed by id."""
    rows = declarations.values() if isinstance(declarations, Mapping) else declarations
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ProfileDeclarationError("each profile declaration must be a mapping")
        unknown = set(row) - _FIELDS
        if unknown:
            raise ProfileDeclarationError(
                f"{row.get('id', '?')}: unknown fields: {', '.join(sorted(unknown))}")
        profile_id = row.get("id")
        if not isinstance(profile_id, str) or not _ID_PATTERN.fullmatch(profile_id):
            raise ProfileDeclarationError(f"invalid profile id: {profile_id!r}")
        if profile_id in indexed:
            raise ProfileDeclarationError(f"duplicate profile id: {profile_id}")
        for field in ("owner", "purpose"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ProfileDeclarationError(f"{profile_id}: {field} must be non-empty text")
        if row.get("status") not in _STATUS:
            raise ProfileDeclarationError(
                f"{profile_id}: status must be one of {', '.join(sorted(_STATUS))}")
        scope = row.get("scope")
        if scope not in _SCOPES:
            raise ProfileDeclarationError(
                f"{profile_id}: scope must be one of {', '.join(sorted(_SCOPES))}")
        tools = _name_list(row.get("tools", []), profile_id, "tools")
        group_ids = _name_list(row.get("groups", []), profile_id, "groups")
        defaults = _name_list(row.get("defaults", []), profile_id, "defaults")
        if scope == "host" and (tools or group_ids or defaults):
            raise ProfileDeclarationError(
                f"{profile_id}: scope=host names every host-supported tool; "
                "tools/groups/defaults must stay empty")
        for reference in [*group_ids, *defaults]:
            if reference not in groups:
                raise ProfileDeclarationError(
                    f"{profile_id}: unknown task group {reference}")
        if set(defaults) - set(group_ids):
            raise ProfileDeclarationError(
                f"{profile_id}: defaults must reference allowed groups")
        indexed[profile_id] = {
            "id": profile_id,
            "owner": row["owner"],
            "status": row["status"],
            "purpose": row["purpose"],
            "scope": scope,
            "tools": tools,
            "groups": group_ids,
            "defaults": defaults,
        }
    return indexed


def merge(*declaration_sets: Mapping[str, Mapping[str, Any]]
          | Iterable[Mapping[str, Any]], groups: Mapping[str, Any],
          ) -> dict[str, dict[str, Any]]:
    """Merge owner sets into one declaration map; duplicate ids are errors."""
    rows: list[Mapping[str, Any]] = []
    for declarations in declaration_sets:
        rows.extend(declarations.values() if isinstance(declarations, Mapping)
                    else declarations)
    return validate(rows, groups=groups)


__all__ = ["ProfileDeclarationError", "merge", "validate"]
