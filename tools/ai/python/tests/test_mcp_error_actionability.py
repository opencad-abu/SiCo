"""Regression contracts for one-shot correction of MCP tool failures."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cadai.circuit_spec_schema import validate
from cadai.mcp import McpServer
from cadai.mcp_call_errors import invalid_arguments, normalize_tool_result
from cadai.mcp_definitions import TOOLS, TOOLS_BY_NAME
from cadai.mcp_input_examples import example_for, render_example
from cadai.skill_diagnostics import record_output


class NoCallClient:
    def call(self, *_args, **_kwargs):
        pytest.fail("invalid MCP call reached the bridge")

    def close(self) -> None:
        pass


def call(server: McpServer, name: str, arguments=None) -> dict:
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params,
    })


def test_unknown_tool_names_the_closest_public_name_and_never_dispatches():
    response = call(McpServer(NoCallClient()), "inspec_schematic", {})
    error = response["error"]
    assert error["code"] == -32602
    assert "inspec_schematic" in error["message"]
    assert "inspect_schematic" in error["message"]
    assert "No tool ran" in error["message"]
    assert error["data"]["code"] == "unknown_tool"
    assert error["data"]["did_you_mean"][0] == "inspect_schematic"
    assert error["data"]["retry_same_request"] is False


def test_profile_denial_is_distinct_from_an_unknown_tool():
    response = call(McpServer(NoCallClient(), profile="verification"), "eval_skill", {
        "code": "1+2",
    })
    error = response["error"]
    assert error["data"]["code"] == "tool_not_available"
    assert error["data"]["profile"] == "verification"
    assert "changing arguments cannot make" in error["message"]


@pytest.mark.parametrize(
    "params, received",
    [
        ([], "array"),
        ({}, "null"),
        ({"arguments": {}}, "null"),
        ({"name": "inspect_schematic", "arguments": []}, "array"),
    ],
)
def test_malformed_call_envelopes_name_the_expected_shape(params, received):
    response = McpServer(NoCallClient()).handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params,
    })
    error = response["error"]
    assert error["code"] == -32602
    assert "No tool ran" in error["message"]
    assert error["data"]["received_type"] == received


def test_missing_and_misspelled_fields_are_reported_in_one_reply():
    response = call(
        McpServer(NoCallClient()),
        "get_pdk_device",
        {"snapshot_ref": "snapshot", "devic_ref": "nmos"},
    )
    message = response["error"]["message"]
    assert "missing device_ref" in message
    assert "unsupported field(s) devic_ref" in message
    assert "did you mean device_ref" in message
    data = response["error"]["data"]
    assert data["retry_same_request"] is False
    assert data["example_source"] == "input_schema"
    assert data["example"]["snapshot_ref"] and data["example"]["device_ref"]
    validate(data["example"], TOOLS_BY_NAME["get_pdk_device"]["inputSchema"])
    assert render_example(data["example"]) in message


def test_type_and_enum_errors_show_received_and_allowed_values():
    wrong_type = call(McpServer(NoCallClient()), "live_model_start", {
        "recipe_id": "r1", "through": "snapshot", "profile": 7,
    })["error"]["message"]
    assert "expected string; got integer" in wrong_type

    wrong_enum = call(McpServer(NoCallClient()), "mcp_help", {
        "action": "lst",
    })["error"]["message"]
    assert "unsupported value lst (got 'lst')" in wrong_enum
    assert "did you mean 'list'" in wrong_enum
    assert "allowed values: list, describe, schema, workflow, example" in wrong_enum


def test_every_advertised_tool_has_actionable_unknown_field_diagnostics():
    for definition in TOOLS:
        rejected = invalid_arguments(
            definition["name"],
            definition["inputSchema"],
            {"__unknown_argument__": True},
        )
        assert rejected is not None, definition["name"]
        message, data = rejected
        assert definition["name"] in message, definition["name"]
        assert "No tool action ran" in message, definition["name"]
        assert "__unknown_argument__" in message, definition["name"]
        assert data["retry_same_request"] is False, definition["name"]


def test_every_advertised_tool_has_a_short_schema_valid_example():
    for definition in TOOLS:
        example = example_for(definition["name"], definition["inputSchema"])
        assert example is not None, definition["name"]
        validate(example, definition["inputSchema"])
        rendered = render_example(example)
        assert rendered is not None, definition["name"]
        assert len(rendered) <= 900, (definition["name"], len(rendered))


def test_all_registered_tools_reject_unknown_fields_before_execution():
    server = McpServer(NoCallClient())
    for name, definition in list(server._tools.items()):
        server._tools[name] = replace(
            definition,
            executor=lambda _name, _arguments: pytest.fail(
                "invalid arguments reached the executor"
            ),
        )
    for name in server._tools:
        response = call(server, name, {"__unknown_argument__": True})
        assert response["error"]["code"] == -32602, name
        assert response["error"]["data"]["code"] == "invalid_arguments", name


def test_every_required_tool_lists_each_missing_top_level_field():
    for definition in TOOLS:
        required = definition["inputSchema"].get("required", [])
        if not required:
            continue
        rejected = invalid_arguments(
            definition["name"], definition["inputSchema"], {}
        )
        assert rejected is not None, definition["name"]
        message, _ = rejected
        for field in required:
            assert field in message, (definition["name"], field)


def test_text_media_types_allow_multiline_without_relaxing_query_fields():
    code = ";" + "x" * 100 + "\nwhen(t list(1))"
    assert invalid_arguments(
        "eval_skill", TOOLS_BY_NAME["eval_skill"]["inputSchema"], {"code": code}
    ) is None
    assert invalid_arguments(
        "check_skill", TOOLS_BY_NAME["check_skill"]["inputSchema"], {"code": code}
    ) is None
    assert invalid_arguments(
        "submit_candidate",
        TOOLS_BY_NAME["submit_candidate"]["inputSchema"],
        {
            "source": "module top;\nendmodule",
            "module": "top",
            "language": "systemverilog",
            "source_generation": "a" * 64,
        },
    ) is None

    rejected = invalid_arguments(
        "list_measurement_capabilities",
        TOOLS_BY_NAME["list_measurement_capabilities"]["inputSchema"],
        {"query": "\n"},
    )
    assert rejected is not None
    assert (
        "control characters" in rejected[0]
        or "surrounding whitespace" in rejected[0]
    )


def test_invalid_arguments_fail_before_the_pdk_guard_or_bridge():
    server = McpServer(NoCallClient())
    server.pdk_selection_guard = lambda _name: pytest.fail("PDK guard was touched")
    response = call(server, "get_pdk_device", {"snapshot_ref": "snapshot"})
    assert response["error"]["data"]["code"] == "invalid_arguments"


def test_rejected_tool_call_keeps_captured_output_and_correction_data():
    server = McpServer(NoCallClient())
    original = server._tools["get_context"]

    def reject(_name, _arguments):
        record_output({
            "output": "captured diagnostic",
            "output_truncated": False,
            "output_capture": "skill_ports",
        })
        raise ValueError("column is required")

    server._tools["get_context"] = replace(
        original, executor=reject, argument_errors=(ValueError,)
    )
    error = call(server, "get_context", {})["error"]
    assert error["data"]["code"] == "invalid_tool_arguments"
    assert error["data"]["tool"] == "get_context"
    assert error["data"]["output"] == "captured diagnostic"
    assert error["data"]["retry_same_request"] is False
    assert error["data"]["example"] == {}
    assert "Correct call example" in error["message"]


def test_domain_failures_gain_identity_and_preserve_a_specific_next_action():
    generic = normalize_tool_result(
        {"code": "snapshot_error", "message": "gone"},
        "query_schematic",
        TOOLS_BY_NAME["query_schematic"]["inputSchema"],
    )
    assert generic["tool"] == "query_schematic"
    assert generic["ok"] is False
    assert generic["retry_same_request"] is False
    assert generic["next_action"].startswith("Resolve the condition")
    assert generic["example"]["snapshot_id"].startswith("snap_")
    assert "Correct call example" in generic["message"]

    specific = normalize_tool_result({
        "code": "pdk_preparation_required",
        "message": "prepare first",
        "next_action": "prepare_pdk_data",
    }, "bind_pdk_device")
    assert specific["next_action"] == "prepare_pdk_data"

    selection = normalize_tool_result({
        "code": "selection_required",
        "message": "choose a library",
        "selection_question": {"id": "pdk", "question": "Which one?"},
    }, "prepare_pdk_data")
    assert selection["next_action"].startswith("Ask the user to answer")

    contradictory = normalize_tool_result(
        {"ok": True, "code": "tool_error", "message": "failed"},
        "query_schematic",
    )
    assert contradictory["ok"] is False


def test_domain_tool_result_carries_example_through_mcp_result():
    server = McpServer(NoCallClient())
    original = server._tools["get_context"]
    server._tools["get_context"] = replace(
        original,
        executor=lambda _name, _arguments: (
            False,
            {"code": "context_error", "message": "context unavailable"},
        ),
    )
    result = call(server, "get_context", {})["result"]
    assert result["isError"] is True
    detail = json.loads(result["content"][0]["text"])
    assert detail["example"] == {}
    assert "Correct call example" in detail["message"]
