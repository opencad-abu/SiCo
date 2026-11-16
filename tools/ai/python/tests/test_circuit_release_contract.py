"""Release audit against actual registration, never a second set of tool schemas."""

import json

from cadai.circuit_tools import REFERENCE_RECIPE_TOOL_NAMES
from cadai.mcp import _TOOLS_BY_NAME, TOOLS, McpServer
from cadai.measurement_catalog import build_catalog
from cadai.project_schema import OPERATIONS, operation_tool


class Offline:
    def call(self, *args):
        raise AssertionError("contract audit must not contact Virtuoso")


def test_registry_schemas_and_capability_links():
    from jsonschema import Draft202012Validator

    assert len(TOOLS) == len(_TOOLS_BY_NAME)
    for definition in TOOLS:
        Draft202012Validator.check_schema(definition["inputSchema"])
    for entry in build_catalog()["entries"]:
        assert entry["tool_input_schema"] == _TOOLS_BY_NAME[entry["tool_name"]]["inputSchema"]
        Draft202012Validator.check_schema(entry["recipe_schema"])
        for name in (
            entry["capture"]["tool_name"],
            entry["capture"]["query_tool"],
            entry["specification"]["tool_name"],
            entry["specification"]["query_tool"],
        ):
            assert name in _TOOLS_BY_NAME
    assert {operation_tool(name) for name in OPERATIONS} <= _TOOLS_BY_NAME.keys()
    for name in OPERATIONS:
        assert "request_id" in _TOOLS_BY_NAME[operation_tool(name)]["inputSchema"]["required"]
        assert not _TOOLS_BY_NAME[operation_tool(name)]["annotations"]["readOnlyHint"]


def test_reference_examples_are_explicitly_outside_generic_operations():
    assert not (REFERENCE_RECIPE_TOOL_NAMES & OPERATIONS.keys())
    for name in REFERENCE_RECIPE_TOOL_NAMES:
        assert _TOOLS_BY_NAME[name]["description"].startswith("Reference validation recipe only.")


def test_nested_operation_errors_are_protocol_errors_without_dispatch(tmp_path):
    server = McpServer(Offline(), workspace=tmp_path)
    for operation in OPERATIONS:
        response = server.handle(
            {
                "id": operation,
                "method": "tools/call",
                "params": {
                    "name": "execute_circuit_operation",
                    "arguments": {
                        "operation": operation,
                        "arguments": {"request_id": "incomplete"},
                    },
                },
            }
        )
        assert response["error"]["code"] == -32602
    response = server.handle(
        {
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "get_circuit_operation",
                "arguments": {
                    "operation": "create_circuit_from_plan",
                    "request_id": "absent",
                    "refresh": False,
                },
            },
        }
    )
    assert response["result"]["isError"]
    detail = json.loads(response["result"]["content"][0]["text"])
    assert detail["code"] == "operation_not_found" and not detail["automatic_resume_allowed"]
    assert not (tmp_path / ".cad").exists()
