"""One resolved host scope: bridge list/dispatch and the enabled_tools filter."""

import json
from types import SimpleNamespace

from sico.adapters.tool_profiles import host_scope
from sico.codex.mcp import ToolBridge
from sico.core.tools import Tool, ToolRegistry
from sico.storage.journal import SessionJournal


def definition(name):
    schema = {"type": "object", "properties": {}, "required": [],
              "additionalProperties": False}
    if name == "execute_circuit_operation":
        schema["properties"] = {
            "operation": {
                "type": "string",
                "enum": [
                    "apply_cdf_update", "create_circuit_from_plan",
                    "create_circuit_library", "create_simulation_setup",
                    "run_simulation_setup",
                ],
            },
            "arguments": {"type": "object"},
        }
    return {
        "name": name,
        "description": name + " fixture",
        "inputSchema": schema,
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    }


def tool(name):
    schema = definition(name)["inputSchema"]
    return Tool(name, name + " fixture", schema, lambda _args: None,
                lambda _args, _context: None)


def registry(*names):
    tools = ToolRegistry()
    for name in names:
        tools.register(tool(name))
    return tools


def schemas(*names):
    return [definition(name) for name in names]


def test_host_scope_filters_registered_tools_and_reports_facts():
    rows = schemas("get_project_context", "list_windows", "live_model_status",
                   "create_circuit_from_plan")
    core = host_scope("desktop_core", rows)
    assert core["tools"] == ("get_project_context", "list_windows")
    assert "create_circuit_from_plan" not in core["tools"]
    compat = host_scope("desktop_compat", rows)
    states = {entry["name"]: entry["state"] for entry in compat["missing"]}
    assert states == {"live_model_status": "unavailable"}
    assert compat["tools"] == ("get_project_context", "list_windows",
                               "create_circuit_from_plan")


def test_host_scope_carries_the_journalled_operations_of_its_members():
    scope = host_scope("circuit",
                       schemas("execute_circuit_operation", "create_circuit_from_plan",
                               "apply_cdf_update", "run_simulation_setup"))
    assert "create_circuit_from_plan" in scope["operations"]
    # The cdf_edit group puts the instance CDF update pair in the circuit
    # scope; the operation follows its target tool into the allowlist.
    assert "apply_cdf_update" in scope["operations"]
    assert "run_simulation_setup" not in scope["operations"]
    maintenance = host_scope("pdk_maintenance",
                             schemas("execute_circuit_operation", "apply_cdf_update"))
    assert "apply_cdf_update" in maintenance["operations"]


def test_bridge_list_and_call_follow_the_scope():
    calls = []

    def execute(name, arguments, metadata=None):
        calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    tools = registry("list_windows", "inspect_window", "create_circuit_from_plan")
    tools.alias("old_window_reader", "list_windows")
    tools.alias("legacy_creator", "create_circuit_from_plan")
    scope = {"profile": "fixture", "tools": ("list_windows",),
             "operations": ()}
    bridge = ToolBridge(tools, execute, scope=scope)
    try:
        assert [row["name"] for row in bridge.dispatch({"method": "list"})["tools"]] == [
            "list_windows"]
        assert bridge.dispatch({"method": "call", "name": "list_windows",
                                "arguments": {}})["isError"] is False
        assert calls == [("list_windows", {})]
        # An alias to a member stays usable; an alias to a non-member does not.
        assert bridge.dispatch({"method": "call", "name": "old_window_reader",
                                "arguments": {}})["isError"] is False
        denied = bridge.dispatch({"method": "call", "name": "legacy_creator",
                                  "arguments": {}})
        assert denied["isError"] is True
        assert json.loads(denied["content"][0]["text"])["code"] == "tool_outside_profile"
        denied = bridge.dispatch({"method": "call", "name": "create_circuit_from_plan",
                                  "arguments": {}})
        assert denied["isError"] is True
        assert len(calls) == 2
    finally:
        bridge.close()


def test_bridge_blocks_journalled_operations_outside_the_scope():
    calls = []

    def execute(name, arguments, metadata=None):
        calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    tools = registry("execute_circuit_operation")
    scope = {"profile": "fixture", "tools": ("execute_circuit_operation",),
             "operations": ("create_circuit_from_plan",)}
    bridge = ToolBridge(tools, execute, scope=scope)
    try:
        allowed = bridge.dispatch({
            "method": "call", "name": "execute_circuit_operation",
            "arguments": {"operation": "create_circuit_from_plan", "arguments": {}}})
        assert allowed["isError"] is False
        denied = bridge.dispatch({
            "method": "call", "name": "execute_circuit_operation",
            "arguments": {"operation": "apply_cdf_update", "arguments": {}}})
        assert denied["isError"] is True
        assert "apply_cdf_update" in denied["content"][0]["text"]
        assert [name for name, _ in calls] == ["execute_circuit_operation"]
    finally:
        bridge.close()


def test_operation_denial_names_the_required_profile():
    tools = registry("execute_circuit_operation", "create_circuit_library")
    scope = {"profile": "simulation", "tools": ("execute_circuit_operation",),
             "operations": ()}
    bridge = ToolBridge(tools, lambda *a, **k: {}, scope=scope)
    try:
        denied = bridge.dispatch({
            "method": "call", "name": "execute_circuit_operation",
            "arguments": {"operation": "create_circuit_library", "arguments": {}}})
        payload = json.loads(denied["content"][0]["text"])
        assert denied["isError"] is True
        assert payload["code"] == "tool_outside_profile"
        assert payload["required_profile"] in payload["candidate_profiles"]
        assert "circuit" in payload["candidate_profiles"]
        assert "start a new session" in payload["next_action"]
    finally:
        bridge.close()


def test_bridge_blocks_operations_outside_the_active_workflow():
    calls = []
    from cadai.task_groups import GROUPS_BY_ID

    def execute(name, arguments, metadata=None):
        calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    names = sorted(set(GROUPS_BY_ID["library_create"]["tools"])
                   | set(GROUPS_BY_ID["circuit_create"]["tools"])
                   | {"execute_circuit_operation"})
    tools = registry(*names)
    scope = {
        "profile": "circuit",
        "tools": tuple(names),
        "operations": ("create_circuit_library", "create_circuit_from_plan"),
    }
    bridge = ToolBridge(tools, execute, scope=scope,
                        workflow=lambda: "library_create")
    try:
        allowed = bridge.dispatch({
            "method": "call", "name": "execute_circuit_operation",
            "arguments": {"operation": "create_circuit_library", "arguments": {}}})
        assert allowed["isError"] is False
        denied = bridge.dispatch({
            "method": "call", "name": "execute_circuit_operation",
            "arguments": {"operation": "create_circuit_from_plan", "arguments": {}}})
        assert denied["isError"] is True
        payload = json.loads(denied["content"][0]["text"])
        assert payload["code"] == "operation_outside_workflow"
        assert payload["workflow"] == "library_create"
        assert payload["required_workflow"] == "circuit_create"
        assert [name for name, _ in calls] == ["execute_circuit_operation"]
    finally:
        bridge.close()


def test_bridge_adopts_the_operation_workflow_when_allowed():
    calls, adopted = [], []
    from cadai.task_groups import GROUPS_BY_ID

    def execute(name, arguments, metadata=None):
        calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    names = sorted(set(GROUPS_BY_ID["library_create"]["tools"])
                   | set(GROUPS_BY_ID["circuit_create"]["tools"])
                   | {"execute_circuit_operation"})
    scope = {
        "profile": "adaptive",
        "tools": tuple(names),
        "operations": ("create_circuit_library", "create_circuit_from_plan"),
    }
    tools = registry(*names)
    bridge = ToolBridge(
        tools, execute, scope=scope,
        workflow=lambda: "library_create",
        adopt_workflow=lambda workflow, operation: (
            adopted.append((workflow, operation))
            or {"ok": True, "workflow": workflow}
        ),
    )
    try:
        result = bridge.dispatch({
            "method": "call", "name": "execute_circuit_operation",
            "arguments": {"operation": "create_circuit_from_plan", "arguments": {}}})
        assert result["isError"] is False
        assert adopted == [("circuit_create", "create_circuit_from_plan")]
        assert [name for name, _ in calls] == ["execute_circuit_operation"]
    finally:
        bridge.close()


def test_bridge_without_a_scope_keeps_the_unfiltered_surface():
    tools = registry("list_windows", "inspect_window")
    bridge = ToolBridge(tools, lambda *a, **k: {}, scope=None)
    try:
        listed = {row["name"] for row in bridge.dispatch({"method": "list"})["tools"]}
        assert listed == {"list_windows", "inspect_window"}
    finally:
        bridge.close()


def test_runtime_enabled_tools_come_from_the_same_scope(tmp_path, monkeypatch):
    from sico.codex import runtime as runtime_module
    from sico.codex.settings import CodexSettings

    captured = {}

    class App:
        def __init__(self, command, environment, cwd, **_kwargs):
            captured["command"] = command
            self.child = SimpleNamespace(returncode=0)

        def close(self):
            pass

    monkeypatch.setattr(runtime_module, "AppServer", App)
    monkeypatch.setattr(runtime_module.process_monitor, "track", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime_module.process_monitor, "finish", lambda *_a, **_k: None)
    settings = CodexSettings(
        dict(provider="responses", base_url="https://upstream.example/v1", model="m"),
        {"SICO_API_KEY": "upstream-private"},
    )
    monkeypatch.setattr(settings, "executable", lambda: "/bin/true")
    with SessionJournal(tmp_path, "enabled-tools") as journal:
        process = runtime_module.CodexRuntime(
            settings, journal, SimpleNamespace(port=12, token="mcp", timeout=3),
            enabled_tools=("list_windows", "inspect_window"))
        process.close()
    assert "mcp_servers.sico_ic.enabled_tools" \
        f"={json.dumps(['list_windows', 'inspect_window'])}" in captured["command"]
