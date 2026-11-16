"""Contract tests for the single MCP schema registry."""

import pytest

from cadai.mcp_registry import (
    RegistryError,
    ToolArgumentError,
    bind_tools,
    build_registry,
    require_bidirectional_bindings,
    require_registered_names,
    validate_aliases,
    wire_row,
)


def tool(name):
    return {"name": name, "inputSchema": {"type": "object"}}


def test_build_registry_preserves_order_and_shared_definition_identity():
    first = tool("first")
    second = tool("second")

    tools, by_name = build_registry([first], [second])

    assert [item["name"] for item in tools] == ["first", "second"]
    assert by_name["first"] is tools[0]
    assert by_name["second"] is tools[1]


def test_build_registry_replaces_legacy_names_without_duplicate_entries():
    tools, by_name = build_registry(
        [tool("legacy"), tool("stable")],
        replace_names={"legacy"},
        replacements=[tool("legacy")],
    )

    assert [item["name"] for item in tools] == ["stable", "legacy"]
    assert len(tools) == len(by_name) == 2


def test_build_registry_rejects_duplicate_names():
    with pytest.raises(RegistryError, match="duplicate MCP tool name: same"):
        build_registry([tool("same")], [tool("same")])


def test_aliases_resolve_to_one_registered_tool_and_never_shadow():
    _, by_name = build_registry([tool("canonical")])

    validate_aliases(by_name, {"legacy": "canonical"})
    with pytest.raises(RegistryError, match="shadows"):
        validate_aliases(by_name, {"canonical": "canonical"})
    with pytest.raises(RegistryError, match="not registered"):
        validate_aliases(by_name, {"legacy": "missing"})
    with pytest.raises(RegistryError, match="non-empty"):
        validate_aliases(by_name, {"": "canonical"})


def test_require_registered_names_reports_handler_schema_drift():
    _, by_name = build_registry([tool("known")])

    with pytest.raises(RegistryError, match="missing"):
        require_registered_names(by_name, {"known", "missing"})


def test_bidirectional_bindings_reject_missing_and_stale_routes():
    _, by_name = build_registry([tool("known")])
    with pytest.raises(RegistryError, match="missing executor"):
        require_bidirectional_bindings(by_name, set())
    with pytest.raises(RegistryError, match="stale executor"):
        require_bidirectional_bindings(by_name, {"known", "stale"})
    require_bidirectional_bindings(by_name, {"known"})
    bind_tools(by_name, [({"known"}, lambda name, args: (True, args), (), False)])


def test_binding_rejects_overlapping_groups_before_dict_can_hide_duplicates():
    _, schemas = build_registry([tool("known")])
    route = ({"known"}, lambda name, args: (True, args), (), False)
    with pytest.raises(RegistryError, match="duplicate MCP executor: known"):
        bind_tools(schemas, [route, route])


@pytest.mark.parametrize("executor", ["handler_label", None, {}])
def test_binding_requires_executable_routes(executor):
    _, schemas = build_registry([tool("known")])
    with pytest.raises(RegistryError, match="callable"):
        bind_tools(schemas, [({"known"}, executor, (), False)])


def test_bound_invocation_enforces_profile_and_maps_only_declared_errors():
    _, schemas = build_registry([tool("eval_skill")])
    seen = []

    def executor(name, arguments):
        seen.append((name, arguments))
        if arguments.get("invalid"):
            raise ValueError("invalid input")
        raise OSError("broken transport")

    entry = bind_tools(schemas, [({"eval_skill"}, executor, (ValueError,), False)])["eval_skill"]
    assert entry.schema is schemas["eval_skill"]
    with pytest.raises(ToolArgumentError, match="unknown tool"):
        entry.invoke({}, profile="verification")
    assert seen == []
    with pytest.raises(ToolArgumentError, match="invalid input"):
        entry.invoke({"invalid": True}, profile="interactive")
    with pytest.raises(OSError, match="broken transport"):
        entry.invoke({}, profile="interactive")


def test_server_lists_and_invokes_the_same_registration():
    import json
    from dataclasses import replace

    from cadai.mcp import McpServer

    server = McpServer(object())
    original = server._tools["get_context"]
    schema = {
        **original.schema,
        "inputSchema": {
            **original.schema["inputSchema"],
            "properties": {"marker": {"type": "integer"}},
        },
    }
    server._tools["get_context"] = replace(
        original, schema=schema, executor=lambda name, args: (True, {"name": name, **args}),
    )
    listed = server.handle({"id": 1, "method": "tools/list"})["result"]["tools"]
    # The row is the shared four-field projection of the registered definition;
    # domain metadata and help text never reach the wire.
    assert next(t for t in listed if t["name"] == "get_context") == wire_row(schema)
    result = server.handle({"id": 2, "method": "tools/call", "params": {
        "name": "get_context", "arguments": {"marker": 7},
    }})["result"]
    assert json.loads(result["content"][0]["text"]) == {"name": "get_context", "marker": 7}


def test_verification_rejects_every_hidden_tool_before_touching_resources():
    from cadai.agent_profile import VERIFICATION_TOOL_NAMES
    from cadai.mcp import TOOLS, McpServer

    server = McpServer(object(), profile="verification")
    server.pdk_selection_guard = lambda _: pytest.fail("denied tool reached PDK guard")
    listed = server.handle({"id": 1, "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in listed} == VERIFICATION_TOOL_NAMES
    for definition in TOOLS:
        if definition["name"] not in VERIFICATION_TOOL_NAMES:
            response = server.handle({"id": 2, "method": "tools/call", "params": {
                "name": definition["name"], "arguments": {},
            }})
            assert response["error"]["code"] == -32602, definition["name"]
    assert server.snapshot_store is None
    assert server._candidate_ledger is None
    assert server._pdk_session is None
