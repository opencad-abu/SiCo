from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext, RunState, ToolCall
from sico.storage.journal import SessionJournal

from cadai.mcp import _TOOLS_BY_NAME
from cadai.mcp import TOOLS as MCP_TOOLS
from cadai.skill_check_tools import read_skill_file
from cadai.socket_server import RequestFailure


class AssistantBroker:
    def __init__(self):
        self.calls = []

    def assistant_call(self, context, method, code):
        self.calls.append((context.record(), method, code))
        if method == "eval_skill":
            return {"ok": True, "value": "42", "output": ""}
        if method == "eval_skill_native":
            return {"ok": True, "value": "(native-result)", "output": ""}
        raise AssertionError(method)


class NestedAssistantBroker(AssistantBroker):
    def assistant_call(self, context, method, code):
        result = super().assistant_call(context, method, code)
        return {"ok": True, "result": result}


def bound(workspace):
    return BoundContext(
        "instance", "generation", "bound",
        {"cwd": str(workspace), "capabilities": ["assistant_call"]},
    )


def test_original_assistant_eval_is_source_bound_and_preflighted(tmp_path):
    broker = AssistantBroker()
    with SessionJournal(tmp_path, "assistant-eval") as journal:
        registry = context_tools(broker, journal)
        result = registry.execute(
            ToolCall("eval", "eval_skill", {"code": "list(1 2)"}), bound(tmp_path)
        )
        assert result.status == "ok"
        assert result.data["value"] == "42"
        assert broker.calls[0][0]["target_id"] == "bound"
        assert broker.calls[0][1] == "eval_skill"

        rejected = registry.execute(
            ToolCall("bad", "eval_skill", {"code": "list(1"}), bound(tmp_path)
        )
        assert rejected.status == "tool_error"
        assert len(broker.calls) == 1


def test_relay_envelope_is_unwrapped_for_original_mcp_contract(tmp_path):
    with SessionJournal(tmp_path, "assistant-nested") as journal:
        registry = context_tools(NestedAssistantBroker(), journal)
        result = registry.execute(
            ToolCall("eval", "eval_skill", {"code": "list(1 2)"}), bound(tmp_path)
        )
        assert result.status == "ok"
        assert result.data["value"] == "42"


def test_agent_legacy_workflow_returns_capability_error(tmp_path):
    class MissingBackend(AssistantBroker):
        def assistant_call(self, context, method, code):
            assert method == "eval_skill_native"
            assert "if(isCallable('aiMaestroRun)" in code
            return {"ok": True, "value": json.dumps(dict(
                ok=False, code="capability_not_available", function="aiMaestroRun",
                message="Legacy workflow backend is not loaded",
                alternatives=["preview_simulation_recipe", "execute_circuit_operation"]))}

    with SessionJournal(tmp_path, "legacy-guard") as journal:
        registry = context_tools(MissingBackend(), journal)
        result = registry.execute(ToolCall("run", "run_maestro_simulation", dict(
            library="Work", cell="Tb", view="maestro")), bound(tmp_path))
        assert result.status == "tool_error"
        assert result.data["code"] == "capability_not_available"
        assert "execute_circuit_operation" in result.data["alternatives"]


def test_registry_contains_every_original_mcp_definition(tmp_path):
    with SessionJournal(tmp_path, "assistant-registry") as journal:
        registry = context_tools(AssistantBroker(), journal)
        names = {tool["name"] for tool in registry.schemas()}
        assert {tool["name"] for tool in MCP_TOOLS} <= names


def test_cds_help_is_advertised_once_and_legacy_name_still_dispatches(tmp_path):
    with SessionJournal(tmp_path, "assistant-help-names") as journal:
        registry = context_tools(AssistantBroker(), journal)
        names = [tool["name"] for tool in registry.schemas()]
        assert "cds_help" in names and "tool_help" not in names
        assert registry.resolve("tool_help") is registry.resolve("cds_help")
        invalid = registry.execute(
            ToolCall("legacy", "tool_help", {"action": "bogus"}), bound(tmp_path)
        )
        assert invalid.status == "invalid_arguments"


def test_mcp_help_describes_host_surface_and_bridge_limits(tmp_path):
    with SessionJournal(tmp_path, "assistant-mcp-help") as journal:
        registry = context_tools(AssistantBroker(), journal)
        listed = registry.execute(
            ToolCall("help", "mcp_help", {"action": "list", "query": "live"}), bound(tmp_path)
        )
        assert listed.status == "ok"
        states = {row["name"]: row["availability"] for row in listed.data["tools"]}
        assert states["live_model_start"] == "unavailable"

        host = registry.execute(
            ToolCall("help", "mcp_help",
                     {"action": "describe", "tool": "get_project_context"}),
            bound(tmp_path),
        )
        assert host.status == "ok"
        assert host.data["tool"]["availability"]["state"] == "available"
        assert registry.resolve("get_project_context") is not None


def test_mcp_help_reports_the_profile_required_for_a_tool(tmp_path):
    with SessionJournal(tmp_path, "assistant-profile-help") as journal:
        journal.append("codex.tool.profile",
                       {"profile": "simulation", "source": "session"},
                       RunState(bound(tmp_path)))
        registry = context_tools(AssistantBroker(), journal)
        described = registry.execute(
            ToolCall("help", "mcp_help",
                     {"action": "describe", "tool": "create_circuit_library"}),
            bound(tmp_path),
        )
        assert described.status == "ok"
        availability = described.data["tool"]["availability"]
        assert availability["state"] == "requires_profile"
        assert availability["required_profile"] == "circuit"
        assert described.data["tool"]["session_profile"] == "simulation"

        operation = registry.execute(
            ToolCall("help", "mcp_help",
                     {"action": "describe", "tool": "execute_circuit_operation"}),
            bound(tmp_path),
        )
        assert operation.status == "ok"
        assert operation.data["tool"]["allowed_operations"] == [
            "create_simulation_setup", "run_simulation_setup"]


def test_mcp_help_keeps_structured_group_suggestions(tmp_path):
    with SessionJournal(tmp_path, "assistant-group-suggestion") as journal:
        registry = context_tools(AssistantBroker(), journal)
        result = registry.execute(
            ToolCall("help", "mcp_help", {"action": "workflow", "group": "circuit"}),
            bound(tmp_path),
        )
        assert result.status == "unknown_group"
        assert result.data["did_you_mean"] == "circuit_create"
        assert "library_create" in result.data["all_groups"]


def test_mcp_help_exposes_small_task_tools_without_a_workflow(tmp_path):
    with SessionJournal(tmp_path, "assistant-small-task") as journal:
        registry = context_tools(AssistantBroker(), journal)
        for name in ("inspect_cdf", "bind_pdk_device", "prepare_pdk_data"):
            described = registry.execute(
                ToolCall("help", "mcp_help",
                         {"action": "describe", "tool": name}),
                bound(tmp_path),
            )
            assert described.status == "ok"
            assert described.data["tool"]["availability"]["state"] == "available"
            assert described.data["tool"]["session_profile"] == "desktop_compat"
            assert described.data["tool"]["active_workflow"] is None


def test_local_mcp_failures_use_the_actionable_error_envelope(tmp_path):
    with SessionJournal(tmp_path, "assistant-local-errors") as journal:
        registry = context_tools(AssistantBroker(), journal)
        search = registry.execute(
            ToolCall("local-search", "Search", {
                "mode": "content", "pattern": "x", "path": "../../outside",
            }),
            bound(tmp_path),
        )
        assert search.status == "search_unavailable"
        assert search.data["tool"] == "Search"
        assert search.data["code"] == "search_unavailable"
        assert search.data["retry_same_request"] is False
        assert search.data["next_action"]
        assert search.data["example"]["mode"] == "content"
        assert "Correct call example" in search.summary

        reference = registry.execute(
            ToolCall("local-api", "get_skill_api", {"name": "notAnExactApi"}),
            bound(tmp_path),
        )
        assert reference.status == "invalid_arguments"
        assert reference.data["tool"] == "get_skill_api"
        assert reference.data["code"] == "invalid_arguments"
        assert reference.data["retry_same_request"] is False
        assert "search_skill_api" in reference.summary
        assert "Correct call example" in reference.summary


def test_mcp_help_refreshes_the_active_workflow_after_selection(tmp_path):
    with SessionJournal(tmp_path, "assistant-workflow-refresh") as journal:
        registry = context_tools(AssistantBroker(), journal)

        before = registry.execute(
            ToolCall("help", "mcp_help",
                     {"action": "describe", "tool": "execute_circuit_operation"}),
            bound(tmp_path),
        )
        assert before.data["tool"]["active_workflow"] is None

        from sico.adapters.session_workflow import enter
        assert enter(journal, "library_create", registry.mcp_schemas(),
                     bound(tmp_path))["ok"] is True

        after = registry.execute(
            ToolCall("help", "mcp_help",
                     {"action": "describe", "tool": "execute_circuit_operation"}),
            bound(tmp_path),
        )
        assert after.data["tool"]["active_workflow"] == "library_create"
        assert after.data["tool"]["allowed_operations"] == ["create_circuit_library"]


def test_legacy_schema_help_and_mcp_help_read_one_catalog(tmp_path):
    with SessionJournal(tmp_path, "assistant-schema-agreement") as journal:
        registry = context_tools(AssistantBroker(), journal)
        legacy = registry.execute(
            ToolCall("schema", "get_circuit_operation_schema",
                     {"operation": "create_circuit_config"}),
            bound(tmp_path),
        )
        helped = registry.execute(
            ToolCall("help", "mcp_help",
                     {"action": "schema", "tool": "create_circuit_config"}),
            bound(tmp_path),
        )
        assert legacy.status == "ok" and helped.status == "ok"
        assert legacy.data["definition"] == _TOOLS_BY_NAME["create_circuit_config"]
        assert helped.data["complete"]
        assert helped.data["schema"] == legacy.data["definition"]["inputSchema"]
        assert helped.data["dispatch"]["tool"] == legacy.data["dispatch_tool"]


def test_mcp_help_serves_host_task_groups(tmp_path):
    """Host-owned groups are injected next to the host's tool registry."""
    with SessionJournal(tmp_path, "assistant-host-groups") as journal:
        registry = context_tools(AssistantBroker(), journal)
        found = registry.execute(
            ToolCall("groups", "mcp_help",
                     {"action": "list", "entity": "groups", "query": "库管理器"}),
            bound(tmp_path),
        )
        assert found.status == "ok"
        assert [row["id"] for row in found.data["groups"]] == ["virtuoso_ui"]

        scoped = registry.execute(
            ToolCall("groups", "mcp_help",
                     {"action": "list", "group": "virtuoso_ui", "query": "window"}),
            bound(tmp_path),
        )
        assert scoped.status == "ok"
        assert {row["name"] for row in scoped.data["tools"]} == {
            "list_windows", "inspect_window"}

        described = registry.execute(
            ToolCall("groups", "mcp_help", {"action": "describe", "group": "virtuoso_ui"}),
            bound(tmp_path),
        )
        assert described.status == "ok"
        assert described.data["available"] and described.data["missing"] == []
        assert "get_project_context" in [row["name"] for row in described.data["members"]]

        results = registry.execute(
            ToolCall("groups", "mcp_help",
                     {"action": "describe", "group": "results_analyze"}),
            bound(tmp_path),
        )
        assert results.data["available"]
        assert {route["when"] for route in results.data["conditions"]} == {"需要波形或 AC 指标"}


def test_live_model_tools_report_controller_boundary(tmp_path):
    with SessionJournal(tmp_path, "assistant-live") as journal:
        registry = context_tools(AssistantBroker(), journal)
        result = registry.execute(
            ToolCall("live", "live_model_status", {}), bound(tmp_path)
        )
        assert result.status == "tool_error"
        assert result.data["code"] == "live_unavailable"


def test_eval_not_a_function_failure_carries_the_funcall_hint(tmp_path):
    class FunctionObjectBroker(AssistantBroker):
        def assistant_call(self, context, method, code):
            return {
                "ok": False,
                "error": '("*Error* eval: not a function" ddsOpenLibManager())',
                "output": "*Error* eval: not a function - ddsOpenLibManager()\n",
            }

    with SessionJournal(tmp_path, "assistant-eval-hint") as journal:
        registry = context_tools(FunctionObjectBroker(), journal)
        result = registry.execute(
            ToolCall("eval", "eval_skill", {"code": "(ddsOpenLibManager())"}), bound(tmp_path)
        )
    assert result.status == "tool_error"
    assert "funcall (getd 'ddsOpenLibManager)" in result.data["hint"]


def test_legacy_tool_failure_surfaces_the_reason(tmp_path):
    class BrokenBroker(AssistantBroker):
        def assistant_call(self, context, method, code):
            raise RuntimeError("SKILL API database is unavailable")

    with SessionJournal(tmp_path, "assistant-failure") as journal:
        registry = context_tools(BrokenBroker(), journal)
        result = registry.execute(
            ToolCall("eval", "eval_skill", {"code": "list(1 2)"}), bound(tmp_path)
        )
    assert result.status == "tool_error"
    assert "SKILL API database is unavailable" in result.summary


def test_skill_file_absolute_path_cannot_escape_workspace(tmp_path):
    outside = tmp_path.parent / "outside.il"
    outside.write_text("list(1 2)", encoding="utf-8")
    with pytest.raises(RequestFailure, match="inside the workspace"):
        read_skill_file(str(outside), tmp_path)


def test_original_assistant_snapshot_artifact_stays_in_private_spool(tmp_path):
    class ArtifactBroker(AssistantBroker):
        def assistant_call(self, context, method, code):
            self.calls.append((context.record(), method, code))
            match = re.search(r'"([^"]*snapshot-data-[0-9a-f]+\.jsonl)"', code)
            assert match, code
            path = Path(match.group(1))
            assert path.parent.name == "assistant-spool"
            assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
            path.write_text(
                '{"record_type":"header","schema_version":"cad.schematic.snapshot.v1",'
                '"kind":"schematic","cellview":{"lib":"Work","cell":"Top",'
                '"view_name":"schematic","oa_view_type":"schematic",'
                '"oa_view_type_source":"platform_api"},"hierarchy_included":false}\n'
                '{"record_type":"footer","counts":{"instances":0,"nets":0,"terminals":0},"truncated":false}\n',
                encoding="utf-8",
            )
            return {"ok": True, "value": json.dumps({"artifact_path": str(path)})}

    broker = ArtifactBroker()
    with SessionJournal(tmp_path, "assistant-snapshot") as journal:
        registry = context_tools(broker, journal)
        previous = os.umask(0o002)
        try:
            result = registry.execute(
                ToolCall("snapshot", "snapshot_schematic", {}), bound(tmp_path)
            )
        finally:
            os.umask(previous)
        assert result.status == "ok"
        artifact = result.data["artifact"]
        assert artifact["size"] > 0
        assert len(artifact["sha256"]) == 64
        assert Path(artifact["path"]).is_file()
        assert Path(artifact["path"]).stat().st_mode & 0o777 == 0o600
