"""Group-scoped help views over the injected task-group declarations.

Group search narrows the query scope only; it never grants or removes a
capability and never widens an explicit ``group`` silently.  A scoped miss
reports the scope it searched, the related groups worth trying and the fact
that the capability may still exist outside the group.  Closure, availability
and digests come from ``task_group_resolver``; this module only shapes the
bounded answers.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from typing import Any

from . import mcp_examples
from . import mcp_help_queries as queries
from .socket_server import RequestFailure
from .task_group_resolver import resolve
from .task_group_views import (
    GROUP_LIST_BYTES,
    GROUP_LIST_LIMIT,
    describe,
    search,
    workflow,
)

Groups = Mapping[str, Mapping[str, Any]]
Catalog = Mapping[str, Mapping[str, Any]]


def _unknown_group(group_id: str, declarations: Groups):
    """One actionable miss: valid ids and the closest declared group."""
    names = sorted(declarations)
    data = {"all_groups": names}
    close = difflib.get_close_matches(group_id, names, n=1)
    if close:
        data["did_you_mean"] = close[0]
    raise RequestFailure(
        "unknown_group",
        f"no such group: {group_id}；有效组见 all_groups",
        data=data,
    )


def _summary_or_view(group: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": group["id"], "purpose": group["purpose"], "owner": group["owner"],
        "status": group["status"], "tool_count": group["tool_count"],
        "bytes": group["bytes"], "available": group["available"],
        "missing": [row["name"] for row in group["missing"]],
    }


def group_search(declarations: Groups, catalog: Catalog, availability,
                 query: str | None, limit: int) -> dict[str, Any]:
    """Rank groups for one task query, or list the declared groups."""
    limit = min(limit, GROUP_LIST_LIMIT)
    if not query:
        groups = [resolve(group_id, declarations=declarations, catalog=catalog,
                          availability=availability)
                  for group_id in declarations]
        window = groups[:limit]
        result: dict[str, Any] = {
            "ok": True, "action": "list", "entity": "groups", "scope": "groups",
            "matched": len(groups), "count": len(window),
            "complete": len(window) >= len(groups),
            "truncated": len(window) < len(groups),
            "groups": [_summary_or_view(group) for group in window],
        }
        if result["truncated"]:
            result["note"] = ("仅返回已声明组的前几条；缩小 query 可按任务词检索。"
                              "组是检索单位，不是权限边界。")
        return queries.bounded(result, GROUP_LIST_BYTES, "groups")
    found = search(query, declarations=declarations, catalog=catalog,
                   availability=availability, limit=limit)
    result = {
        "ok": True, "action": "list", "entity": "groups", "scope": "groups",
        "query": query, "matched": found["matched"],
        "count": len(found["groups"]), "groups": found["groups"],
        "complete": found["matched"] <= len(found["groups"]),
    }
    for key in ("hint", "all_groups", "truncated", "note"):
        if key in found:
            result[key] = found[key]
    return queries.bounded(result, GROUP_LIST_BYTES, "groups")


def group_view(declarations: Groups, catalog: Catalog, availability,
               group_id: str, offset: int) -> dict[str, Any]:
    """Bounded group view: members, conditions, missing abilities, recovery."""
    if group_id not in declarations:
        _unknown_group(group_id, declarations)
    view = describe(group_id, declarations=declarations, catalog=catalog,
                    availability=availability, offset=offset)
    return {"ok": True, "action": "describe", **view}


def _related_groups(declarations: Groups, catalog: Catalog, availability,
                    group_id: str, query: str | None) -> list[str]:
    related = [reference for reference in declarations[group_id].get("subgroups", [])]
    if query:
        for candidate in search(query, declarations=declarations, catalog=catalog,
                                availability=availability,
                                limit=GROUP_LIST_LIMIT)["groups"]:
            if candidate["id"] != group_id and candidate["id"] not in related:
                related.append(candidate["id"])
    return related[:GROUP_LIST_LIMIT]


def scoped_tools(declarations: Groups, catalog: Catalog, availability,
                 group_id: str, query: str | None, offset: int,
                 limit: int) -> dict[str, Any]:
    """List one group's resolved members; a miss explains the scope it searched."""
    if group_id not in declarations:
        _unknown_group(group_id, declarations)
    group = resolve(group_id, declarations=declarations, catalog=catalog,
                    availability=availability)
    conditional = {row["when"]: [] for row in group["conditions"]}
    for condition in group["conditions"]:
        conditional[condition["when"]] = condition["tools"]
    terms = [term.lower() for term in (query or "").split() if term]

    def matches(name: str) -> bool:
        # Inside a group the member names are the working vocabulary; the
        # global list keeps description matching for discovery-by-purpose.
        row = catalog.get(name)
        haystack = " ".join(str((row or {}).get(key, ""))
                            for key in ("name", "title")).lower()
        return all(term in haystack for term in terms)

    rows = []
    for name in group["tools"]:
        if matches(name):
            rows.append({"name": name, "purpose": queries.purpose(catalog[name]),
                         "availability": queries.availability(name, availability)["state"]})
    extended = []
    for when, names in conditional.items():
        for name in names:
            if name in catalog and matches(name):
                extended.append({"name": name, "when": when,
                                 "availability": queries.availability(name, availability)["state"]})
    window = rows[offset: offset + limit]
    result: dict[str, Any] = {
        "ok": True, "action": "list", "entity": "tools", "scope": f"group:{group_id}",
        "group": _summary_or_view(group), "query": query or "",
        "matched": len(rows), "offset": offset, "count": len(window),
        "complete": offset + len(window) >= len(rows),
        "truncated": offset + len(window) < len(rows),
        "tools": window,
    }
    if result["truncated"]:
        result["next_offset"] = offset + len(window)
    if extended:
        result["conditional_tools"] = extended
        result["conditional_note"] = "这些是条件扩展成员，只在对应前提成立时才需要。"
    if not rows and not extended:
        result["hint"] = ("组内未命中，不代表 SiCo 不具备该能力：可去掉 group 做全局"
                          "查询、换用相关组，或直接用精确工具名调用/查询。")
        related = _related_groups(declarations, catalog, availability, group_id, query)
        if related:
            result["related_groups"] = related
    return queries.bounded(result, queries.LIST_BYTES, "tools")


def workflow_view(declarations: Groups, catalog: Catalog, availability,
                  group_id: str) -> dict[str, Any]:
    """Hand-off notes and execution routes for one group."""
    if group_id not in declarations:
        _unknown_group(group_id, declarations)
    view = workflow(group_id, declarations=declarations, catalog=catalog,
                    availability=availability)
    examples_pointer = {
        "action": "example", "group": group_id,
        "tools": [row["tool"] for row in mcp_examples.group_rows(view["members"])],
    }
    view["members"] = [
        {"tool": name, **queries.dispatch_entry(name, catalog)}
        for name in view.pop("members")]
    if examples_pointer["tools"]:
        view["examples"] = examples_pointer
    routes = []
    for condition in view.pop("conditions", []):
        for route in condition["routes"]:
            entry = {"when": condition["when"], "from": condition["from"],
                     "tool": route["tool"]}
            if "operation" in route:
                entry["operation"] = route["operation"]
            entry["dispatch"] = queries.dispatch_entry(route["tool"], catalog)
            routes.append(entry)
    view["conditional_routes"] = routes
    return {"ok": True, "action": "workflow", **view}

def example_listing(declarations: Groups, catalog: Catalog, availability,
                    group_id: str) -> dict[str, Any]:
    """Curated examples among one group's resolved members, bounded and explicit."""
    if group_id not in declarations:
        _unknown_group(group_id, declarations)
    group = resolve(group_id, declarations=declarations, catalog=catalog,
                    availability=availability)
    rows = mcp_examples.group_rows(group["tools"])
    result: dict[str, Any] = {
        "ok": True, "action": "example", "group": group_id, "examples": rows,
        "count": len(rows), "complete": True, "truncated": False,
    }
    if rows:
        result["note"] = "按工具获取完整样例：action=example, tool=<name>。"
    else:
        result["hint"] = "该组暂无受控样例；schema 仍是权威模式，组内成员可直接调用。"
    return queries.bounded(result, mcp_examples.BUDGET_BYTES, "examples")


__all__ = ["example_listing", "group_search", "group_view", "scoped_tools", "workflow_view"]
