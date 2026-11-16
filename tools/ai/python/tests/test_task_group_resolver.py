"""Declaration contract, closure and search for task groups."""

from __future__ import annotations

import json

import pytest

from cadai.task_group_resolver import resolve
from cadai.task_group_schema import GroupDeclarationError, merge, validate
from cadai.task_group_views import (
    GROUP_LIST_BYTES,
    describe,
    groups_for_tool,
    search,
)
from cadai.task_groups import GROUPS as CADAI_GROUPS


def group(group_id, **overrides):
    row = {
        "id": group_id,
        "owner": "cadai",
        "status": "draft",
        "purpose": f"{group_id} purpose",
        "keywords": ["示例", group_id],
        "tools": [],
        "subgroups": [],
        "conditions": [],
        "recovery": [],
        "connections": ["fixture note"],
    }
    row.update(overrides)
    return row


def tool(name, operation_enum=None):
    properties = {}
    if operation_enum is not None:
        properties["operation"] = {"type": "string", "enum": operation_enum}
    return {"name": name, "description": f"{name} description",
            "inputSchema": {"type": "object", "properties": properties},
            "annotations": {"readOnlyHint": True}}


def catalog(*rows):
    return {row["name"]: row for row in rows}


def test_validate_requires_the_declared_shape():
    with pytest.raises(GroupDeclarationError, match="unknown fields"):
        validate([group("a", extra=1)])
    with pytest.raises(GroupDeclarationError, match="invalid group id"):
        validate([group("Bad-Id")])
    with pytest.raises(GroupDeclarationError, match="status"):
        validate([group("a", status="candidate")])
    with pytest.raises(GroupDeclarationError, match="keywords"):
        validate([group("a", keywords=["", " "])])
    with pytest.raises(GroupDeclarationError, match="invalid member"):
        validate([group("a", tools=[3])])
    with pytest.raises(GroupDeclarationError, match="condition"):
        validate([group("a", conditions=[{"when": "x"}])])
    with pytest.raises(GroupDeclarationError, match="unknown subgroup"):
        validate([group("a", subgroups=["ghost"])])
    with pytest.raises(GroupDeclarationError, match="duplicate"):
        validate([group("a"), group("a")])


def test_validate_rejects_nesting_and_self_reference():
    nested = [group("top", subgroups=["leaf"]), group("leaf", subgroups=["other"]),
              group("other")]
    with pytest.raises(GroupDeclarationError, match="nests beyond one level"):
        validate(nested)
    with pytest.raises(GroupDeclarationError, match="itself"):
        validate([group("a", subgroups=["a"])])


def test_merge_detects_cross_owner_duplicates():
    merged = merge([group("a")], [group("b")])
    assert sorted(merged) == ["a", "b"]
    with pytest.raises(GroupDeclarationError, match="duplicate group id: a"):
        merge([group("a")], [group("a")])


def test_resolve_expands_subgroups_keeps_conditions_and_recovery():
    declarations = validate([
        group("leaf", tools=["one", "two"], recovery=["two"]),
        group("combo", tools=["three"], subgroups=["leaf"],
              conditions=[{"when": "需要扩展", "tools": ["four"]}]),
    ])
    rows = catalog(tool("one"), tool("two"), tool("three"), tool("four"))
    combo = resolve("combo", declarations=declarations, catalog=rows)
    assert combo["tools"] == ["three", "one", "two"]
    assert combo["tool_count"] == 3 and combo["available"]
    assert combo["conditional"] == ["four"]
    assert combo["conditions"] == [
        {"when": "需要扩展", "tools": ["four"], "routes": [{"tool": "four"}],
         "from": "combo"}]
    assert combo["recovery"] == ["two"]
    assert combo["digest"].startswith("sha256:") and combo["bytes"] > 0


def test_resolve_reports_missing_members_with_reasons():
    declarations = validate([group("g", tools=["here", "ghost"],
                                   conditions=[{"when": "x", "tools": ["phantom"]}])])
    rows = catalog(tool("here"))
    entry = resolve("g", declarations=declarations, catalog=rows)
    assert entry["tools"] == ["here"]
    assert [row["name"] for row in entry["missing"]] == ["ghost"]
    assert entry["missing"][0]["state"] == "unavailable"
    assert not entry["available"]
    assert [row["name"] for row in entry["conditional_missing"]] == ["phantom"]


def test_resolve_validates_operations_against_the_tool_schema():
    declarations = validate([group("g", tools=[
        {"tool": "op_entry", "operation": "create_x"},
        {"tool": "op_entry", "operation": "nope"},
        {"tool": "plain", "operation": "anything"},
    ])])
    rows = catalog(tool("op_entry", operation_enum=["create_x"]), tool("plain"))
    entry = resolve("g", declarations=declarations, catalog=rows)
    assert entry["tools"] == ["op_entry"]
    reasons = {row["name"]: row["reason"] for row in entry["missing"]}
    assert "operation 不在该工具 schema 内" in reasons["op_entry"]
    assert "没有 operation 枚举" in reasons["plain"]


def test_resolve_rejects_recovery_outside_the_group():
    declarations = validate([group("g", tools=["one"], recovery=["outsider"])])
    with pytest.raises(GroupDeclarationError, match="recovery members must be part"):
        resolve("g", declarations=declarations, catalog=catalog(tool("one")))


def test_search_matches_task_phrases_in_both_languages():
    declarations = validate([
        group("tb", keywords=["创建TB", "测试平台", "testbench"]),
        group("ui", keywords=["库管理器", "library manager"]),
    ])
    rows = catalog()
    assert [row["id"] for row in
            search("创建测试平台", declarations=declarations, catalog=rows)["groups"]] == ["tb"]
    assert [row["id"] for row in
            search("Library Manager", declarations=declarations, catalog=rows)["groups"]] == ["ui"]
    miss = search("nothing matches here", declarations=declarations, catalog=rows)
    assert miss["matched"] == 0 and "不代表 SiCo 不具备该能力" in miss["hint"]
    assert miss["all_groups"] == ["tb", "ui"]


def test_search_and_describe_stay_inside_their_budgets():
    declarations = validate([group("many", tools=[f"t{index:02d}" for index in range(40)])])
    rows = catalog(*(tool(f"t{index:02d}") for index in range(40)))
    entry = describe("many", declarations=declarations, catalog=rows)
    assert not entry["complete"] and entry["truncated"]
    assert len(entry["members"]) <= 20
    assert len(json.dumps(entry, ensure_ascii=False).encode()) <= 8192
    listed = search("示例", declarations=declarations, catalog=rows)
    assert len(json.dumps(listed, ensure_ascii=False).encode()) <= GROUP_LIST_BYTES


def test_groups_for_tool_is_generated_from_declarations():
    declarations = validate([
        group("a", tools=["shared"]),
        group("b", tools=["shared", "other"]),
        group("c", tools=["other"]),
    ])
    rows = catalog(tool("shared"), tool("other"))
    assert groups_for_tool("shared", declarations=declarations, catalog=rows) == ["a", "b"]
    assert groups_for_tool("other", declarations=declarations, catalog=rows) == ["b", "c"]
    assert groups_for_tool("missing", declarations=declarations, catalog=rows) == []


def test_cadai_declarations_resolve_against_the_canonical_catalog():
    """The committed declarations stay valid against the real catalog."""
    from cadai.mcp_help import catalog as canonical_catalog

    declarations = validate(CADAI_GROUPS)
    rows = canonical_catalog()
    allowed_draft = {"library_create"}
    for group_id in declarations:
        entry = resolve(group_id, declarations=declarations, catalog=rows)
        assert entry["missing"] == [], (group_id, entry["missing"])
        assert entry["conditional_missing"] == [], (group_id, entry["conditional_missing"])
        assert entry["tool_count"] > 0
        assert entry["status"] in {"reviewed", "draft"}
        if entry["status"] == "draft":
            assert group_id in allowed_draft


def test_sico_group_statuses_reflect_real_task_acceptance():
    """virtuoso_ui/results_analyze passed their families; report stays workbench-only."""
    from sico.adapters.task_groups import TASK_GROUPS

    assert TASK_GROUPS["virtuoso_ui"]["status"] == "reviewed"
    assert TASK_GROUPS["results_analyze"]["status"] == "reviewed"
    assert TASK_GROUPS["engineering_report"]["status"] == "draft"
