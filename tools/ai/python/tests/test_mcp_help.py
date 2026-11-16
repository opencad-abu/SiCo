"""Contract tests for the read-only capability help (mcp_help)."""

from __future__ import annotations

import json

import pytest

from cadai.mcp import TOOLS, McpServer
from cadai.mcp.mcp_help import LIST_BYTES, mcp_help
from cadai.platform.io.socket_server import RequestFailure


def tool(name, description="Fixture tool", schema=None):
    return {
        "name": name,
        "description": description,
        "inputSchema": schema or {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    }


def test_list_is_bounded_sorted_and_searchable():
    rows = {f"tool_{index:02d}": tool(f"tool_{index:02d}", f"Fixture {index}")
            for index in range(40)}
    listed = mcp_help({"action": "list"}, catalog_rows=rows)
    assert listed["total"] == 40 and listed["count"] == 20
    assert listed["truncated"] and listed["next_offset"] == 20
    assert len(json.dumps(listed, ensure_ascii=False).encode()) <= LIST_BYTES

    page = mcp_help({"action": "list", "offset": 20}, catalog_rows=rows)
    assert page["complete"] and not page["truncated"] and page["count"] == 20

    found = mcp_help({"action": "list", "query": "FIXTURE 37"}, catalog_rows=rows)
    assert {row["name"] for row in found["tools"]} == {"tool_37"}
    empty = mcp_help({"action": "list", "query": "nothing matches"}, catalog_rows=rows)
    assert empty["total"] == 0 and empty["tools"] == [] and empty["complete"]


def test_oversized_limit_is_clamped_after_client_validation():
    rows = {f"tool_{index:02d}": tool(f"tool_{index:02d}", f"Fixture {index}")
            for index in range(40)}
    listed = mcp_help({"action": "list", "limit": 10_000}, catalog_rows=rows)
    assert listed["count"] == 20 and listed["truncated"]
    public = next(row for row in TOOLS if row["name"] == "mcp_help")["inputSchema"]
    assert "maximum" not in public["properties"]["limit"]
    assert "maximum" not in public["properties"]["offset"]


def test_empty_entity_defaults_to_the_tool_catalog():
    rows = {"tool": tool("tool")}
    listed = mcp_help({"action": "list", "entity": ""}, catalog_rows=rows)
    assert listed["total"] == 1 and listed["tools"][0]["name"] == "tool"


def test_canonical_catalog_advertises_both_help_tools():
    names = {row["name"] for row in TOOLS}
    assert {"cds_help", "mcp_help"} <= names
    found = mcp_help({"action": "list", "query": "cadence manual"})
    assert [row["name"] for row in found["tools"]] == ["cds_help"]


def test_pdk_budget_governance_keeps_details_on_demand():
    """Move budget-only prose to help without losing the operational detail."""
    advertised = {row["name"]: row["description"] for row in TOOLS}
    details = {
        "bind_pdk_device": ("rectangular pin figures", "callback_order"),
        "import_pdk_facts": ("legacy standard checkpoint",),
        "preview_simulation_recipe": ("plot=true", "Monte Carlo"),
        "create_simulation_setup": ("Retains foreground UI",),
    }
    for name, fragments in details.items():
        result = mcp_help({"action": "describe", "tool": name})
        described = result["tool"]
        assert result["complete"]
        for fragment in fragments:
            assert fragment in described["help"]
            assert fragment not in advertised[name]


def test_unknown_workflow_group_names_the_closest_declared_group():
    with pytest.raises(RequestFailure) as error:
        mcp_help({"action": "workflow", "group": "circuit"})
    assert error.value.code == "unknown_group"
    assert error.value.data["did_you_mean"] == "circuit_create"
    assert "library_create" in error.value.data["all_groups"]

    library = mcp_help({"action": "workflow", "group": "library_create"})
    assert library["id"] == "library_create"
    assert any(row["tool"] == "prepare_library"
               for row in library["members"])
    assert any(row.get("operation") == "create_circuit_library"
               for row in library["members"])


def test_describe_reports_entry_digest_and_legacy_name():
    described = mcp_help({"action": "describe", "tool": "execute_circuit_operation"})["tool"]
    assert described["name"] == "execute_circuit_operation"
    assert described["availability"]["state"] == "available"
    assert described["dispatch"]["tool"] == "execute_circuit_operation"
    assert described["digest"].startswith("sha256:")
    assert "operation" in described["schema_summary"]["properties"]

    operation = mcp_help({"action": "describe", "tool": "create_circuit_from_plan"})["tool"]
    assert operation["dispatch"] == {
        "tool": "execute_circuit_operation",
        "operation": "create_circuit_from_plan",
        "direct_tool": "create_circuit_from_plan",
    }

    # These operations register a same-named schema holder but reject direct
    # writes; help must not advertise a path that the handler forbids.
    for name in ("create_circuit_library", "apply_cdf_update"):
        journaled = mcp_help({"action": "describe", "tool": name})["tool"]
        assert "direct_tool" not in journaled["dispatch"]
        assert journaled["dispatch"]["operation"] == name

    legacy = mcp_help({"action": "describe", "tool": "tool_help"})["tool"]
    assert legacy["name"] == "cds_help" and legacy["legacy_name"] == "tool_help"

    with pytest.raises(RequestFailure) as error:
        mcp_help({"action": "describe", "tool": "no_such_tool"})
    assert error.value.code == "unknown_tool"

    with pytest.raises(RequestFailure) as operation:
        mcp_help({"action": "describe", "tool": "edit_circuit"})
    assert operation.value.code == "operation_not_tool"
    assert "get_circuit_operation_schema" in str(operation.value)
    assert "execute_circuit_operation" in str(operation.value)


def test_describe_reports_host_availability_without_hiding_the_contract():
    rows = {"live_model_start": tool("live_model_start"),
            "get_context": tool("get_context")}

    def availability(name):
        if name == "live_model_start":
            return {"state": "unavailable", "reason": "bridge does not proxy live modeling"}
        return {"state": "available"}

    described = mcp_help({"action": "describe", "tool": "live_model_start"},
                         catalog_rows=rows, availability=availability)["tool"]
    assert described["availability"]["state"] == "unavailable"
    assert "live modeling" in described["availability"]["reason"]
    assert described["purpose"]  # the contract stays readable

    require_path = mcp_help({"action": "describe", "tool": "get_context"},
                            catalog_rows=rows,
                            availability=lambda name: {"state": "requires_profile",
                                                       "reason": "core profile",
                                                       "enable": "use an interactive session"})
    assert require_path["tool"]["availability"] == {
        "state": "requires_profile", "reason": "core profile",
        "enable": "use an interactive session"}


def test_describe_exposes_the_active_workflow_and_its_operations():
    described = mcp_help(
        {"action": "describe", "tool": "execute_circuit_operation"},
        session={
            "profile": "circuit",
            "active_workflow": "library_create",
            "allowed_workflows": ["circuit_create", "library_create"],
            "operations": ["create_circuit_library"],
        },
    )["tool"]
    assert described["session_profile"] == "circuit"
    assert described["active_workflow"] == "library_create"
    assert described["allowed_workflows"] == ["circuit_create", "library_create"]
    assert described["allowed_operations"] == ["create_circuit_library"]


def test_schema_reads_fragments_and_refuses_to_fake_completeness():
    small = tool("small", schema={"type": "object", "properties": {"a": {"type": "string"}}})
    root = mcp_help({"action": "schema", "tool": "small"}, catalog_rows={"small": small})
    assert root["complete"] and root["schema"] == small["inputSchema"]
    assert root["schema_path"] == ""

    fragment = mcp_help({"action": "schema", "tool": "small", "schema_path": "/properties/a"},
                        catalog_rows={"small": small})
    assert fragment["complete"] and fragment["schema"] == {"type": "string"}

    big = tool("big", schema={
        "type": "object",
        "properties": {f"field_{index}": {"type": "string", "description": "x" * 400}
                       for index in range(60)},
    })
    oversized = mcp_help({"action": "schema", "tool": "big"}, catalog_rows={"big": big})
    assert not oversized["complete"] and oversized["truncated"]
    assert "schema" not in oversized  # never a truncated JSON contract
    assert oversized["bytes"] > 8192 and len(oversized["sha256"]) == 64
    children = {row["path"]: row["bytes"] for row in oversized["children"]}
    assert "/properties/field_0" in children and len(children) <= 24
    assert oversized.get("children_truncated") is True
    assert children["/properties/field_0"] > 0

    narrowed = mcp_help({"action": "schema", "tool": "big",
                         "schema_path": "/properties/field_0"}, catalog_rows={"big": big})
    assert narrowed["complete"] and narrowed["schema"]["type"] == "string"

    with pytest.raises(RequestFailure) as missing:
        mcp_help({"action": "schema", "tool": "big", "schema_path": "/properties/nope"},
                 catalog_rows={"big": big})
    assert missing.value.code == "schema_path_not_found"
    with pytest.raises(RequestFailure) as malformed:
        mcp_help({"action": "schema", "tool": "big", "schema_path": "properties"},
                 catalog_rows={"big": big})
    assert malformed.value.code == "invalid_params"


def test_schema_of_circuit_tools_exceeds_the_default_budget():
    """The largest real contracts must be drill-down, not inline payloads."""
    oversized = mcp_help({"action": "schema", "tool": "preview_circuit_geometry"})
    assert not oversized["complete"] and oversized["dispatch"]["tool"] == "preview_circuit_geometry"
    children = [row["path"] for row in oversized["children"]]
    assert "/properties/spec" in children and "/properties/layout" in children


def test_arguments_are_validated():
    for arguments in ({"action": "bogus"}, {"action": "describe"},
                      {"action": "list", "tool": "x"},
                      {"action": "list", "limit": 0}, {"action": "list", "offset": -1},
                      {"action": "schema", "tool": "x", "schema_path": 3},
                      {"action": "list", "shell": True}):
        with pytest.raises(RequestFailure) as error:
            mcp_help(arguments)
        assert error.value.code == "invalid_params"


def test_mcp_help_is_local_and_read_only():
    class Client:
        def call(self, *args):
            pytest.fail("must not contact Virtuoso")

    response = McpServer(Client()).handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "mcp_help", "arguments": {"action": "describe", "tool": "mcp_help"}},
    })
    detail = json.loads(response["result"]["content"][0]["text"])
    assert not response["result"]["isError"] and detail["tool"]["name"] == "mcp_help"
    assert next(t for t in TOOLS if t["name"] == "mcp_help")["annotations"] == {
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    }


def test_results_group_alias_and_result_workflow_examples():
    names = ("bind_result_context", "read_maestro_results", "query_maestro_results")
    rows = {name: tool(name) for name in names}
    groups = {
        "results_analyze": {
            "id": "results_analyze", "owner": "cadai", "status": "draft",
            "purpose": "读取已有结果", "keywords": ["results"],
            "tools": ["read_maestro_results"], "subgroups": [],
            "conditions": [], "recovery": [], "connections": [],
        },
        "simulation_run": {
            "id": "simulation_run", "owner": "cadai", "status": "draft",
            "purpose": "运行仿真", "keywords": ["simulation"],
            "tools": [], "subgroups": [], "conditions": [],
            "recovery": [], "connections": [],
        },
        "circuit_create": {
            "id": "circuit_create", "owner": "cadai", "status": "draft",
            "purpose": "创建电路", "keywords": ["circuit"],
            "tools": [], "subgroups": [], "conditions": [],
            "recovery": [], "connections": [],
        }
    }
    workflow = mcp_help(
        {"action": "workflow", "group": "results"},
        groups=groups, catalog_rows=rows,
    )
    assert workflow["id"] == "results_analyze"
    assert mcp_help(
        {"action": "workflow", "group": "simulation"},
        groups=groups, catalog_rows=rows,
    )["id"] == "simulation_run"
    assert mcp_help(
        {"action": "workflow", "group": "circuit_design"},
        groups=groups, catalog_rows=rows,
    )["id"] == "circuit_create"
    for name in names:
        example = mcp_help({"action": "example", "tool": name}, catalog_rows=rows)
        assert example["tool"] == name
        assert example["arguments"]
        assert example["notes"]


def task_group(group_id, **overrides):
    row = {
        "id": group_id, "owner": "cadai", "status": "draft",
        "purpose": f"{group_id} 用途", "keywords": ["示例", group_id],
        "tools": [], "subgroups": [], "conditions": [], "recovery": [],
        "connections": ["fixture"],
    }
    row.update(overrides)
    return row


def fixture_groups():
    return {
        "read_circuit": task_group("read_circuit", keywords=["读图", "schematic read"],
                                   tools=["inspect_schematic", "query_schematic"],
                                   recovery=["query_schematic"]),
        "open_ui": task_group("open_ui", keywords=["库管理器", "library manager"],
                              tools=["eval_skill", "list_windows"]),
    }


def fixture_catalog(rows):
    return rows


def test_group_search_and_scoped_tools_stay_bounded_and_explicit():
    groups, rows = fixture_groups(), fixture_catalog({
        "inspect_schematic": tool("inspect_schematic", "Read schematic"),
        "query_schematic": tool("query_schematic", "Query a snapshot page"),
        "eval_skill": tool("eval_skill", "Evaluate one SKILL expression"),
        "list_windows": tool("list_windows", "List windows"),
    })
    listed = mcp_help({"action": "list", "entity": "groups", "query": "库管理器"},
                      groups=groups, catalog_rows=rows)
    assert [row["id"] for row in listed["groups"]] == ["open_ui"]

    scoped = mcp_help({"action": "list", "group": "read_circuit", "query": "schematic"},
                      groups=groups, catalog_rows=rows)
    assert scoped["scope"] == "group:read_circuit"
    assert {row["name"] for row in scoped["tools"]} == {"inspect_schematic", "query_schematic"}
    assert scoped["group"]["id"] == "read_circuit"

    miss = mcp_help({"action": "list", "group": "read_circuit", "query": "library manager"},
                    groups=groups, catalog_rows=rows)
    assert miss["matched"] == 0 and miss["tools"] == []
    assert "不代表 SiCo 不具备该能力" in miss["hint"]
    assert miss["related_groups"] == ["open_ui"]

    full = mcp_help({"action": "list", "query": "library manager"},
                    groups=groups, catalog_rows=rows)
    # The global tool catalog carries no such words; the group is the way in.
    assert full["tools"] == []


def test_group_describe_and_workflow_report_conditions_and_recovery():
    groups, rows = fixture_groups(), fixture_catalog({
        "inspect_schematic": tool("inspect_schematic", "Read schematic"),
        "query_schematic": tool("query_schematic", "Query a snapshot page"),
        "eval_skill": tool("eval_skill", "Evaluate one SKILL expression"),
        "list_windows": tool("list_windows", "List windows"),
    })
    described = mcp_help({"action": "describe", "group": "read_circuit"},
                         groups=groups, catalog_rows=rows)
    assert described["id"] == "read_circuit" and described["available"]
    assert described["status"] == "draft" and described["digest"].startswith("sha256:")
    assert [row["name"] for row in described["members"]] == [
        "inspect_schematic", "query_schematic"]
    assert described["recovery"] == ["query_schematic"]
    assert described["connections"] == ["fixture"]

    flow = mcp_help({"action": "workflow", "group": "read_circuit"},
                    groups=groups, catalog_rows=rows)
    assert flow["connections"] == ["fixture"]
    assert {row["tool"] for row in flow["members"]} == {"inspect_schematic", "query_schematic"}


def test_circuit_create_workflow_names_the_geometry_evidence_sources():
    """真实 circuit_create 工作流必须给出几何证据来源，并说明 shell 只做文本检索。"""
    flow = mcp_help({"action": "workflow", "group": "circuit_create"})
    text = json.dumps(flow, ensure_ascii=False)
    assert "bind_pdk_device 返回的 geometry 行" in text
    assert "binding_refs" in text and "get_pdk_device" in text
    assert "所有 OpenAccess 数据都通过平台工具（CDNS-IC/EMPY-AE/SNPS-CC）的 API 读写" in text
    assert "grep/find 等文本检索只用于日志与输出文本" in text


def test_group_arguments_are_validated():
    for arguments in (
        {"action": "list", "entity": "services"},
        {"action": "list", "entity": "groups", "group": "read_circuit"},
        {"action": "describe"},
        {"action": "describe", "tool": "inspect_schematic", "group": "read_circuit"},
        {"action": "workflow"},
        {"action": "schema", "tool": "inspect_schematic", "group": "read_circuit"},
    ):
        with pytest.raises(RequestFailure) as error:
            mcp_help(arguments)
        assert error.value.code == "invalid_params"
    with pytest.raises(RequestFailure) as unknown:
        mcp_help({"action": "describe", "group": "ghost"})
    assert unknown.value.code == "unknown_group"


def test_default_groups_answer_the_design_examples_for_cadai():
    """The committed cadai declarations answer the design query examples."""
    found = mcp_help({"action": "list", "entity": "groups", "query": "创建测试平台"})
    assert [row["id"] for row in found["groups"]] == ["testbench_create"]

    scoped = mcp_help({"action": "list", "group": "testbench_create", "query": "symbol"})
    assert scoped["scope"] == "group:testbench_create"
    assert {row["name"] for row in scoped["tools"]} == {
        "inspect_symbol_ports", "preview_template_symbol", "get_symbol_binding",
        "inspect_template_symbol"}

    flow = mcp_help({"action": "workflow", "group": "simulation_run"})
    dispatch = {row["tool"]: row for row in flow["members"]}
    assert {"inspect_simulation_setup", "get_simulation_recipe_status"} <= set(dispatch)
    assert "simulation_job" not in dispatch
    routes = {route["tool"]: route for route in flow["conditional_routes"]}
    assert routes["execute_circuit_operation"]["operation"] == "run_simulation_setup"
    assert routes["execute_circuit_operation"]["dispatch"] == {
        "tool": "execute_circuit_operation"}
    assert flow["recovery"] == ["get_simulation_recipe_status"]


def test_describe_serves_on_demand_help_that_is_never_advertised():
    """Long-form detail lives on the definition and is served by describe only."""
    described = mcp_help({"action": "describe", "tool": "bind_pdk_device"})
    assert described["tool"]["help"] and "netlist_terminal_map" in described["tool"]["help"]

    class Client:
        def call(self, *args):
            pytest.fail("must not contact Virtuoso")

    rows = McpServer(Client()).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    row = next(t for t in rows if t["name"] == "bind_pdk_device")
    assert set(row) == {"name", "description", "inputSchema", "annotations"}
