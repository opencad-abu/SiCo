"""One immutable eligible snapshot for one tool profile.

``Eligible = Registered ∩ HostSupported ∩ SessionAllowed``: the profile
declaration supplies the session scope (explicit tools plus the required,
conditional and recovery members of its referenced task groups, or every
registered tool for ``scope=host``), the injected availability supplies the
host facts, and the catalog supplies registration and definition bytes.
Nothing here starts a service, executes a tool or mutates global state; the
same declarations and facts always resolve to the same snapshot digest.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .task_group_resolver import resolve as resolve_group


class ExposureError(ValueError):
    """A profile that cannot be resolved into one snapshot."""


def _state(availability, name: str) -> dict[str, Any]:
    if availability is None:
        return {"state": "available"}
    record = availability(name)
    if not isinstance(record, Mapping):
        return {"state": "unavailable", "reason": "宿主未报告可用性"}
    return dict(record)


def _bytes(names, catalog: Mapping[str, Mapping[str, Any]]) -> int:
    total = 0
    for name in names:
        row = catalog[name]
        wire = {key: row.get(key) for key in
                ("name", "description", "inputSchema", "annotations")}
        total += len(json.dumps(wire, ensure_ascii=False, allow_nan=False,
                                sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return total


def snapshot(profile_id: str, *, declarations: Mapping[str, Mapping[str, Any]],
             groups: Mapping[str, Mapping[str, Any]],
             catalog: Mapping[str, Mapping[str, Any]], availability=None) -> dict[str, Any]:
    """Resolve one profile against one catalog and one set of host facts."""
    if profile_id not in declarations:
        raise ExposureError(f"unknown tool profile: {profile_id}")
    declaration = declarations[profile_id]

    required: list[str] = []
    conditional: list[str] = []
    recovery: list[str] = []
    missing: list[dict[str, Any]] = []
    used_groups = list(declaration.get("groups", []))
    if declaration["scope"] == "host":
        required.extend(catalog)
    else:
        for group_id in used_groups:
            view = resolve_group(group_id, declarations=groups, catalog=catalog,
                                 availability=availability)
            for bucket, names in (("required", view["tools"]),
                                  ("conditional", view["conditional"]),
                                  ("recovery", view["recovery"])):
                target = {"required": required, "conditional": conditional,
                          "recovery": recovery}[bucket]
                target.extend(name for name in names if name not in target)
            for entry in [*view["missing"], *view["conditional_missing"],
                          *view["recovery_missing"]]:
                missing.append({**entry, "profile": profile_id, "group": group_id})
        required.extend(name for name in declaration.get("tools", [])
                        if name not in required)

    declared = list(dict.fromkeys([*required, *conditional, *recovery]))
    eligible, unsupported = [], []
    for name in declared:
        record = _state(availability, name)
        if name in catalog and record.get("state") == "available":
            eligible.append(name)
        elif name not in catalog:
            unsupported.append({"name": name, "state": "unregistered",
                                "reason": "宿主未注册此工具"})
        else:
            unsupported.append({"name": name,
                                "state": record.get("state", "unavailable"),
                                "reason": record.get("reason", "宿主不可用")})
    seen = {entry["name"] for entry in missing}
    missing.extend(entry for entry in unsupported if entry["name"] not in seen)

    order = list(catalog)
    eligible.sort(key=order.index)
    payload = {
        "profile": profile_id,
        "scope": declaration["scope"],
        "groups": used_groups,
        "defaults": list(declaration.get("defaults", [])),
        "required": [name for name in required if name in eligible],
        "conditional": [name for name in conditional if name in eligible],
        "recovery": [name for name in recovery if name in eligible],
        "tools": eligible,
        "missing": missing,
    }
    encoded = json.dumps({**payload, "members": {name: catalog[name] for name in eligible}},
                         ensure_ascii=False, allow_nan=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    resolved = {key: tuple(value) if kind is tuple else value
                for key, value, kind in (
                    ("profile", profile_id, str),
                    ("scope", declaration["scope"], str),
                    ("groups", used_groups, tuple),
                    ("defaults", declaration.get("defaults", []), tuple),
                    ("required", [name for name in required if name in eligible], tuple),
                    ("conditional", [name for name in conditional if name in eligible], tuple),
                    ("recovery", [name for name in recovery if name in eligible], tuple),
                    ("tools", eligible, tuple),
                    ("missing", missing, list),
                )}
    return {
        **resolved,
        "digest": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        "bytes": _bytes(eligible, catalog),
    }


def allowed_operations(tools, operations: Mapping[str, str]) -> tuple[str, ...]:
    """Journalled operations whose target tool is inside the resolved snapshot.

    ``execute_circuit_operation`` dispatches an inner tool; the operation is
    allowed only when that target tool is a member, so a profile cannot be
    widened through the journal.  ``operations`` maps operation -> target tool.
    """
    members = set(tools)
    return tuple(sorted(operation for operation, tool in operations.items()
                        if tool in members))


__all__ = ["ExposureError", "allowed_operations", "snapshot"]
