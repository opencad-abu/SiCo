"""Keep MCP policy ceilings fixed and bind change reviews to exact contracts."""

from __future__ import annotations


def ledger_binding(entry, old, new):
    kind = entry.get("kind")
    source = entry.get("from") if kind == "renamed" else entry.get("name")
    target = entry.get("to") if kind == "renamed" else entry.get("name")
    return (entry.get("before_sha256") == old.get(source)
            and entry.get("after_sha256") == new.get(target)
            and "before_sha256" in entry and "after_sha256" in entry)


def profile_contract_errors(report, budget):
    contracts = budget.get("profile_contracts")
    if contracts is None:  # Historical v1 snapshots and synthetic fixtures.
        return []
    current = {row["id"]: row for row in report["tool_profiles"]["profiles"]}
    errors = []
    if set(current) != set(contracts):
        errors.append("profile contract inventory differs from the measured profiles")
    for name, entry in contracts.items():
        row = current.get(name, {})
        if entry.get("digest") != row.get("digest") or entry.get("count") != row.get("tool_count"):
            errors.append(f"profile {name}: contract changed without a matching review")
        for field in ("owner", "reason"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                errors.append(f"profile {name}: contract review requires {field}")
    return errors


def check_history(current, previous):
    """A fresh freeze is a proposal, never permission to relax committed policy."""
    if previous is None:
        return ["MCP budget has no comparison snapshot"]
    errors = []
    # These are the original measurement and budgets, not moving acceptance data.
    for key in ("format", "frozen_at", "method", "description_budget", "class_limits",
                "layers", "allowed_missing", "host_unsupported", "removals"):
        if current.get(key) != previous.get(key):
            errors.append(f"MCP policy reset is forbidden: {key}; remedy the finding instead")
    old_profiles = previous.get("profiles", {})
    new_profiles = current.get("profiles", {})
    removed = set(old_profiles) - set(new_profiles)
    if removed:
        errors.append(
            "MCP profile budget inventory cannot remove profiles: "
            + ", ".join(sorted(removed)))
    contracts = current.get("profile_contracts", {})
    for name in sorted(set(new_profiles) - set(old_profiles)):
        entry = contracts.get(name)
        if (not isinstance(entry, dict) or not entry.get("owner")
                or not entry.get("reason")):
            errors.append(
                f"MCP new profile {name} requires a reviewed profile_contracts entry")
    for name, row in current.get("profiles", {}).items():
        before = old_profiles.get(name)
        if before is None:
            continue
        if any(row.get(key, 0) > before.get(key, 0) for key in ("count", "bytes", "budget")):
            errors.append(f"MCP profile ceiling cannot grow: {name}")
        if before.get("status") == "ok" and row.get("status") != "ok":
            errors.append(f"MCP new embedded profile debt is forbidden: {name}")
    old_debt = {row["tool"]: row for row in previous.get("debt", {}).get("tools", [])}
    for row in current.get("debt", {}).get("tools", []):
        before = old_debt.get(row["tool"])
        if (before is None or row["bytes"] > before["bytes"]
                or row["limit"] != before["limit"] or row["class"] != before["class"]):
            errors.append(f"MCP new/increased embedded tool debt is forbidden: {row['tool']}")
    for key, identity, digest in (
        ("changes", lambda row: (row["layer"], row.get("name"), row["kind"], row.get("from")),
         "after_sha256"),
        ("profile_contracts", lambda row: row["id"], "digest"),
    ):
        def rows(payload):
            value = payload.get(key, [] if key == "changes" else {})
            return value if isinstance(value, list) else [dict(row, id=name) for name, row in value.items()]
        old = {identity(row): row for row in rows(previous)}
        for row in rows(current):
            before = old.get(identity(row))
            if before and before.get(digest) != row.get(digest) and before.get(digest):
                if row.get("reason") == before.get("reason"):
                    errors.append(f"MCP {key} review must explain the new contract: {identity(row)}")
        if key == "profile_contracts" and old and not current.get(key):
            errors.append("MCP profile contract reviews cannot be removed")
    return errors
