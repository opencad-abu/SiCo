"""Bounded query views over validated task-group declarations.

Search, group describe, workflow and the reverse tool index present one
declaration set; they never grant or remove a capability and never widen an
explicit group scope silently.  Declaration validation and closure live in
``task_group_resolver``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .task_group_resolver import resolve

GROUP_LIST_LIMIT = 5
GROUP_LIST_BYTES = 4096
GROUP_DESCRIBE_BYTES = 8192
GROUP_MEMBER_ROWS = 20
PURPOSE_CHARS = 200


def _summary(groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "id": group["id"],
        "purpose": group["purpose"][:PURPOSE_CHARS],
        "owner": group["owner"],
        "status": group["status"],
        "tool_count": group["tool_count"],
        "bytes": group["bytes"],
        "available": group["available"],
        "missing": [row["name"] for row in group["missing"]],
    } for group in groups]


def _score(declaration: Mapping[str, Any], terms: Sequence[str]) -> int:
    haystacks = {
        "id": declaration["id"].lower(),
        "purpose": declaration["purpose"].lower(),
        "keywords": " ".join(declaration.get("keywords", [])).lower(),
    }
    words = [word.lower() for word in declaration.get("keywords", [])
             if len(word.strip()) >= 2]
    score = 0
    for term in terms:
        # A task phrase ("创建测试平台") usually contains the declared keyword
        # ("测试平台"), so both directions count as a keyword hit.
        keyword = term in haystacks["keywords"] or any(word in term for word in words)
        if not (keyword or term in haystacks["id"] or term in haystacks["purpose"]):
            return 0
        score += 30 if keyword else 0
        score += 20 if term in haystacks["id"] else 0
        score += 10 if term in haystacks["purpose"] else 0
    return score


def search(query: str, *, declarations: Mapping[str, Mapping[str, Any]],
           catalog: Mapping[str, Mapping[str, Any]], availability=None,
           limit: int = GROUP_LIST_LIMIT) -> dict[str, Any]:
    """Rank groups by keywords for one task query; never a permission decision."""
    terms = [term.lower() for term in query.split() if term]
    scored = []
    for group_id in declarations:
        score = _score(declarations[group_id], terms)
        if score:
            scored.append((score, group_id))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    resolved = [resolve(group_id, declarations=declarations, catalog=catalog,
                        availability=availability)
                for _, group_id in scored[:limit]]
    result: dict[str, Any] = {"matched": len(scored), "groups": _summary(resolved)}
    if not scored:
        result["hint"] = ("组检索未命中不代表 SiCo 不具备该能力；可省略 query 查看全部组，"
                          "或用精确工具名直接调用/查询。")
        result["all_groups"] = sorted(declarations)[:GROUP_LIST_LIMIT]
    if len(scored) > limit:
        result["truncated"] = True
        result["note"] = "只返回最相关的候选组；缩小 query 可减少候选。"
    return result


def describe(group_id: str, *, declarations: Mapping[str, Mapping[str, Any]],
             catalog: Mapping[str, Mapping[str, Any]], availability=None,
             offset: int = 0) -> dict[str, Any]:
    """Bounded group view: members, conditions, missing abilities, recovery."""
    group = resolve(group_id, declarations=declarations, catalog=catalog,
                    availability=availability)
    window = group["tools"][offset: offset + GROUP_MEMBER_ROWS]
    result = {
        "id": group["id"], "owner": group["owner"], "status": group["status"],
        "purpose": group["purpose"], "keywords": group["keywords"],
        "subgroups": group["subgroups"],
        "available": group["available"],
        "tool_count": group["tool_count"], "bytes": group["bytes"],
        "digest": group["digest"],
        "members": [{"name": name,
                     "state": (availability(name) or {}).get("state", "available")
                     if availability is not None else "available"}
                    for name in window],
        "offset": offset, "count": len(window),
        "complete": offset + len(window) >= group["tool_count"],
        "conditions": group["conditions"],
        "conditional": group["conditional"],
        "recovery": group["recovery"],
        "missing": group["missing"],
        "conditional_missing": group["conditional_missing"],
        "connections": group["connections"],
    }
    if not result["complete"]:
        result["truncated"] = True
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    while len(encoded) > GROUP_DESCRIBE_BYTES and result["members"]:
        result["members"].pop()
        result["count"] = len(result["members"])
        result["complete"] = False
        result["truncated"] = True
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
    return result


def workflow(group_id: str, *, declarations: Mapping[str, Mapping[str, Any]],
             catalog: Mapping[str, Mapping[str, Any]], availability=None) -> dict[str, Any]:
    """The group's declared hand-off notes plus its execution-relevant facts.

    Dispatch entries are the help layer's authority (``mcp_help_queries``);
    this view stays with declarations, closure and recovery only.
    """
    group = resolve(group_id, declarations=declarations, catalog=catalog,
                    availability=availability)
    return {
        "id": group_id, "status": group["status"], "purpose": group["purpose"],
        "connections": group["connections"],
        "recovery": group["recovery"],
        "missing": group["missing"],
        "members": group["tools"],
        "conditional": group["conditional"],
        "conditions": group["conditions"],
    }


def groups_for_tool(name: str, *, declarations: Mapping[str, Mapping[str, Any]],
                    catalog: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Reverse index generated from declarations; no tool carries its own list."""
    return sorted(group_id for group_id in declarations
                  if name in resolve(group_id, declarations=declarations,
                                     catalog=catalog)["tools"])


__all__ = [
    "GROUP_DESCRIBE_BYTES", "GROUP_LIST_BYTES", "GROUP_LIST_LIMIT", "GROUP_MEMBER_ROWS",
    "describe", "groups_for_tool", "search", "workflow",
]
