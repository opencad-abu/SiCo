from __future__ import annotations

import json
import re

import pytest

from cadai.mcp import McpServer
from cadai.workflows.design_delivery.workflow_tools import (
    MUTATING_WORKFLOW_TOOL_NAMES,
    READ_ONLY_WORKFLOW_TOOL_NAMES,
    WORKFLOW_TOOL_NAMES,
    WORKFLOW_TOOLS,
    WorkflowArgumentError,
    build_workflow_skill,
    decode_workflow_result,
)


class FakeClient:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.result = result or {"value": '{"ok":true,"job_id":"cadflow-1"}'}

    def call(self, method: str, arguments: dict[str, object]):
        self.calls.append((method, arguments))
        return True, self.result

    def close(self) -> None:
        pass


def test_workflow_tool_catalog_has_strict_schemas_and_safety_annotations() -> None:
    tools = {tool["name"]: tool for tool in WORKFLOW_TOOLS}
    assert set(tools) == WORKFLOW_TOOL_NAMES
    assert MUTATING_WORKFLOW_TOOL_NAMES.isdisjoint(READ_ONLY_WORKFLOW_TOOL_NAMES)
    for name, tool in tools.items():
        assert tool["inputSchema"]["type"] == "object"
        assert tool["inputSchema"]["additionalProperties"] is False
        annotations = tool["annotations"]
        assert annotations["readOnlyHint"] is (name in READ_ONLY_WORKFLOW_TOOL_NAMES)
        assert annotations["destructiveHint"] is (name in MUTATING_WORKFLOW_TOOL_NAMES)
        assert annotations["idempotentHint"] is (name in READ_ONLY_WORKFLOW_TOOL_NAMES)
        assert annotations["openWorldHint"] is (name in MUTATING_WORKFLOW_TOOL_NAMES)
    path_pattern = tools["run_rce"]["inputSchema"]["properties"]["config_path"]["pattern"]
    assert re.fullmatch(path_pattern, "/runs/config with spaces.toml")
    assert re.fullmatch(path_pattern, "/")
    for invalid_path in ("relative.toml", "/runs/control\t.toml", "/runs/trailing "):
        assert re.fullmatch(path_pattern, invalid_path) is None
    assert (
        tools["create_maestro_testbench"]["inputSchema"]["properties"]["analyses"]["maxItems"] == 32
    )
    create_properties = tools["create_maestro_testbench"]["inputSchema"]["properties"]
    assert create_properties["existing_view_policy"]["enum"] == ["error", "update"]
    analysis = create_properties["analyses"]["items"]
    assert set(analysis["properties"]["name"]["enum"]) == {"dc", "ac", "tran"}
    assert set(create_properties["outputs"]["items"]["properties"]["output_type"]["enum"]) == {
        "point",
        "corners",
        "sweeps",
        "all",
        "terminal",
        "terminalV",
        "net",
    }
    assert len(create_properties["outputs"]["items"]["oneOf"]) == 2
    status_schema = tools["get_maestro_simulation_status"]["inputSchema"]
    assert status_schema["properties"]["job_id"]["maxLength"] == 96
    assert "absolute TOML" in tools["run_rce"]["description"]
    assert "existing schematic testbench" in tools["create_maestro_testbench"]["description"]
    stop_description = tools["stop_maestro_simulation"]["description"]
    assert "active run" in stop_description
    assert "force-close a session retained" in stop_description


def test_maestro_create_accepts_signal_and_expression_output_branches():
    """The output oneOf branches carry type-less enums; they must stay scalar."""
    from cadai.shared.schema_errors import CircuitSpecError
    from cadai.shared.schema_validation import validate

    schema = next(tool for tool in WORKFLOW_TOOLS
                  if tool["name"] == "create_maestro_testbench")["inputSchema"]
    base = {
        "setup_library": "SicoTest", "setup_cell": "tb", "setup_view": "maestro_ac",
        "test_name": "ac", "testbench_library": "SicoTest", "testbench_cell": "tb",
        "testbench_view": "schematic", "simulator": "spectre",
        "analyses": [{"name": "ac", "options": [{"name": "start", "value": "1"}]}],
    }
    validate({**base, "outputs": [
        {"name": "Y", "output_type": "net", "signal_name": "/Y", "save": True}
    ]}, schema)
    validate({**base, "outputs": [
        {"name": "Y", "output_type": "point", "expression": "Y", "save": True}
    ]}, schema)
    with pytest.raises(CircuitSpecError, match="unsupported value bogus"):
        validate({**base, "outputs": [
            {"name": "Y", "output_type": "bogus", "signal_name": "/Y"}
        ]}, schema)


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        (
            "run_rce",
            {"config_path": '/runs/a"b.toml', "dry_run": True, "stop_after": "extract"},
            'aiWorkflowStart("rce" "/runs/a\\"b.toml" "run" t "extract")',
        ),
        (
            "run_drc",
            {"config_path": "/runs/drc.toml", "mode": "generate", "stop_after": "gds"},
            'aiWorkflowStart("drc" "/runs/drc.toml" "generate" nil "gds")',
        ),
        (
            "run_lvs",
            {"config_path": "/runs/lvs.toml", "stop_after": "lvs"},
            'aiWorkflowStart("lvs" "/runs/lvs.toml" "run" nil "lvs")',
        ),
        (
            "stream_gds",
            {"config_path": "/runs/gds.toml", "mode": "generate"},
            'aiWorkflowStart("gds" "/runs/gds.toml" "generate" nil nil)',
        ),
        (
            "export_cdl",
            {"config_path": "/runs/cdl.toml"},
            'aiWorkflowStart("cdl" "/runs/cdl.toml" "run" nil nil)',
        ),
        (
            "generate_lef",
            {"config_path": "/runs/lef.toml", "dry_run": False},
            'aiWorkflowStart("lef" "/runs/lef.toml" "run" nil nil)',
        ),
        (
            "analyze_dspf",
            {"source_path": "/runs/top.dspf"},
            'aiWorkflowAnalyzeDspf("/runs/top.dspf")',
        ),
        (
            "get_cad_flow_status",
            {"job_id": "cad-flow-123"},
            'aiWorkflowStatus("cad-flow-123")',
        ),
        (
            "run_maestro_simulation",
            {"library": "work", "cell": "tb", "view": "maestro"},
            'aiMaestroRun("work" "tb" "maestro" "single")',
        ),
        (
            "run_maestro_simulation",
            {
                "library": "work",
                "cell": "tb",
                "view": "maestro",
                "run_mode": "monte_carlo",
            },
            'aiMaestroRun("work" "tb" "maestro" "monte_carlo")',
        ),
        (
            "get_maestro_simulation_status",
            {"job_id": "maestro-123"},
            'aiMaestroStatus("maestro-123")',
        ),
        (
            "stop_maestro_simulation",
            {"job_id": "maestro-123"},
            'aiMaestroStop("maestro-123")',
        ),
    ],
)
def test_workflow_builders_emit_only_fixed_skill_calls(
    name: str, arguments: dict[str, object], expected: str
) -> None:
    assert build_workflow_skill(name, arguments) == expected


def test_legacy_status_guard_names_the_durable_replacements() -> None:
    code = build_workflow_skill(
        "get_maestro_simulation_status",
        {"job_id": "maestro-123"},
        guard_unavailable=True,
    )
    assert "capability_not_available" in code
    assert "get_simulation_recipe_status" in code
    assert "eda_job" in code


def test_mcp_routes_workflow_as_fixed_eval_and_decodes_json() -> None:
    client = FakeClient()
    response = McpServer(client).handle(
        {
            "jsonrpc": "2.0",
            "id": "workflow",
            "method": "tools/call",
            "params": {
                "name": "run_drc",
                "arguments": {"config_path": "/runs/drc.toml", "stop_after": "drc"},
            },
        }
    )
    assert client.calls == [
        (
            "eval_skill",
            {"code": 'aiWorkflowStart("drc" "/runs/drc.toml" "run" nil "drc")'},
        )
    ]
    assert response["result"]["isError"] is False
    assert json.loads(response["result"]["content"][0]["text"])["job_id"] == "cadflow-1"


def test_mcp_preserves_workflow_error_and_rejects_before_client_call() -> None:
    failed_client = FakeClient({"value": '{"ok":false,"error":{"code":"already_running"}}'})
    failed = McpServer(failed_client).handle(
        {
            "jsonrpc": "2.0",
            "id": "failed",
            "method": "tools/call",
            "params": {
                "name": "run_lvs",
                "arguments": {"config_path": "/runs/lvs.toml"},
            },
        }
    )
    assert failed["result"]["isError"] is True

    invalid_client = FakeClient()
    invalid = McpServer(invalid_client).handle(
        {
            "jsonrpc": "2.0",
            "id": "invalid",
            "method": "tools/call",
            "params": {
                "name": "run_rce",
                "arguments": {"config_path": "relative.toml"},
            },
        }
    )
    assert invalid["error"]["code"] == -32602
    assert invalid_client.calls == []


def test_maestro_builder_rejects_schema_valid_request_over_eval_byte_limit() -> None:
    arguments = _minimal_maestro(
        outputs=[
            {
                "name": f"result_{index}",
                "output_type": "point",
                "expression": "x" * 4_096,
            }
            for index in range(64)
        ]
    )
    with pytest.raises(WorkflowArgumentError, match="exceeds 65536 UTF-8 bytes"):
        build_workflow_skill("create_maestro_testbench", arguments)


def test_maestro_create_encodes_bounded_nested_literals() -> None:
    code = build_workflow_skill(
        "create_maestro_testbench",
        {
            "setup_library": "results",
            "setup_cell": "amp_tb",
            "setup_view": "maestro_ai",
            "existing_view_policy": "update",
            "test_name": "nominal",
            "testbench_library": "work",
            "testbench_cell": "amp_tb",
            "testbench_view": "schematic",
            "simulator": "spectre",
            "analyses": [
                {
                    "name": "tran",
                    "enabled": True,
                    "options": [
                        {"name": "stop", "value": "1u"},
                        {"name": "step", "value": "1n"},
                    ],
                }
            ],
            "variables": [{"name": "VDD", "value": "1.2"}],
            "model_files": [
                "/models/base.scs",
                {"path": "/models/corners.scs", "section": "tt"},
            ],
            "stimulus_files": ["/stimuli/input.scs"],
            "outputs": [
                {
                    "name": "vout",
                    "output_type": "terminalV",
                    "signal_name": "/out",
                    "plot": True,
                    "save": True,
                    "specs": {"minimum": "0.8", "gt": "0.5"},
                },
                {
                    "name": "gain",
                    "output_type": "point",
                    "expression": 'value(v("/out") 1u)',
                },
            ],
            "corners": [
                {
                    "name": "tt",
                    "enabled": True,
                    "enabled_tests": ["nominal"],
                    "disabled_tests": ["unused"],
                    "variables": [{"name": "TEMP", "value": "27"}],
                }
            ],
        },
    )
    assert code == (
        'aiMaestroCreate("results" "amp_tb" "maestro_ai" "update" "nominal" '
        '"work" "amp_tb" "schematic" "spectre" '
        'list(list("tran" t list(list("stop" "1u") list("step" "1n")))) '
        'list(list("VDD" "1.2")) '
        'list("/models/base.scs" list("/models/corners.scs" "tt")) '
        'list("/stimuli/input.scs") '
        'list(list("vout" "terminalV" "/out" nil t t '
        'list(list("minimum" "0.8") list("gt" "0.5"))) '
        'list("gain" "point" nil "value(v(\\"/out\\") 1u)" nil nil nil)) '
        'list(list("tt" t list("nominal") list("unused") list(list("TEMP" "27")))))'
    )


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("unknown", {}, "unsupported workflow tool"),
        ("run_rce", {"config_path": "relative.toml"}, "absolute path"),
        ("run_rce", {"config_path": " /runs/rce.toml"}, "trimmed"),
        ("run_rce", {"config_path": "/runs/rce.toml\n"}, "trimmed"),
        ("run_rce", {"config_path": "/runs/rce.toml", "extra": 1}, "unsupported field"),
        ("run_rce", {"config_path": "/runs/rce.toml", "dry_run": 1}, "boolean"),
        ("run_rce", {"config_path": "/runs/rce.toml", "mode": True}, "one of"),
        ("run_rce", {"config_path": "/runs/rce.toml", "stop_after": "drc"}, "not valid"),
        (
            "run_rce",
            {"config_path": "/runs/rce.toml", "mode": "generate", "dry_run": True},
            "does not accept",
        ),
        (
            "run_rce",
            {"config_path": "/runs/rce.toml", "mode": "generate", "stop_after": "cdl"},
            "does not accept",
        ),
        (
            "stream_gds",
            {"config_path": "/runs/gds.toml", "stop_after": "gds"},
            "unsupported field",
        ),
        ("analyze_dspf", {"source_path": "/runs/a\tdspf"}, "control character"),
        ("get_cad_flow_status", {"job_id": False}, "string"),
        ("get_maestro_simulation_status", {"job_id": "x" * 97}, "exceeds 96"),
        (
            "run_maestro_simulation",
            {"library": "w", "cell": "tb", "view": "m", "run_mode": "netlist"},
            "one of",
        ),
    ],
)
def test_workflow_builder_rejects_ambiguous_or_unsafe_arguments(
    name: str, arguments: dict[str, object], message: str
) -> None:
    with pytest.raises(WorkflowArgumentError, match=message):
        build_workflow_skill(name, arguments)


def _minimal_maestro(**updates: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "setup_library": "results",
        "setup_cell": "tb",
        "setup_view": "maestro",
        "test_name": "nominal",
        "testbench_library": "work",
        "testbench_cell": "tb",
        "testbench_view": "schematic",
        "analyses": [{"name": "tran"}],
    }
    arguments.update(updates)
    return arguments


@pytest.mark.parametrize(
    ("output_type", "fields", "expected"),
    [
        ("point", {"expression": "1"}, '"point" nil "1"'),
        ("corners", {"expression": "1"}, '"corners" nil "1"'),
        ("sweeps", {"expression": "1"}, '"sweeps" nil "1"'),
        ("all", {"expression": "1"}, '"all" nil "1"'),
        ("terminal", {"signal_name": "/out"}, '"terminal" "/out" nil'),
        ("terminalV", {"signal_name": "/out"}, '"terminalV" "/out" nil'),
        ("net", {"signal_name": "/out"}, '"net" "/out" nil'),
    ],
)
def test_maestro_output_types_follow_mae_add_output_contract(
    output_type: str, fields: dict[str, str], expected: str
) -> None:
    output = {"name": "result", "output_type": output_type, **fields}
    code = build_workflow_skill("create_maestro_testbench", _minimal_maestro(outputs=[output]))
    assert expected in code


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"existing_view_policy": "replace"}, "one of"),
        ({"analyses": []}, "between 1 and"),
        ({"analyses": [{"name": "noise"}]}, "one of"),
        ({"analyses": [{"name": "tran", "enabled": 1}]}, "must be a boolean"),
        ({"analyses": [{"name": "tran", "extra": "x"}]}, "unsupported field"),
        ({"analyses": [{"name": "tran", "options": [True]}]}, "items must be objects"),
        ({"model_files": ["models.scs"]}, "absolute path"),
        ({"model_files": [{"path": "/models.scs"}]}, "section is required"),
        ({"stimulus_files": ["stimulus.scs"]}, "absolute path"),
        (
            {"outputs": [{"name": "vout", "output_type": "net"}]},
            "requires only signal_name",
        ),
        (
            {
                "outputs": [
                    {
                        "name": "vout",
                        "output_type": "terminal",
                        "signal_name": "/out",
                        "expression": "1",
                    }
                ]
            },
            "requires only signal_name",
        ),
        (
            {
                "outputs": [
                    {
                        "name": "gain",
                        "output_type": "point",
                        "expression": "1",
                        "specs": {"unsupported": "2"},
                    }
                ]
            },
            "unsupported field",
        ),
        (
            {
                "corners": [
                    {
                        "name": "tt",
                        "enabled_tests": ["nominal"],
                        "disabled_tests": ["nominal"],
                    }
                ]
            },
            "same test",
        ),
        ({"variables": [{"name": "VDD", "value": True}]}, "must be a string"),
        ({"outputs": [{"name": "bad", "output_type": "signal"}]}, "one of"),
        (
            {"outputs": [{"name": "vout", "output_type": "net", "signal_name": "x" * 257}]},
            "exceeds 256",
        ),
        ({"analyses": [{"name": "tran"}] * 33}, "between 1 and"),
    ],
)
def test_maestro_builder_rejects_invalid_nested_data(
    updates: dict[str, object], message: str
) -> None:
    with pytest.raises(WorkflowArgumentError, match=message):
        build_workflow_skill("create_maestro_testbench", _minimal_maestro(**updates))


@pytest.mark.parametrize(
    ("detail", "ok", "code"),
    [
        ({"value": '{"ok":true,"job_id":"1"}'}, True, None),
        ({"value": '{"ok":false,"code":"busy"}'}, False, "busy"),
        ({"value": "[]"}, False, "invalid_result"),
        ({"value": "not-json"}, False, "invalid_result"),
        ({"value": 1}, False, "invalid_result"),
        ({"value": "{}", "value_truncated": True}, False, "result_too_large"),
        ({"value": "{}", "spooled": True}, False, "result_too_large"),
    ],
)
def test_workflow_result_decoder_is_bounded(
    detail: dict[str, object], ok: bool, code: str | None
) -> None:
    actual_ok, payload = decode_workflow_result(detail)
    assert actual_ok is ok
    if code is not None:
        assert payload["code"] == code
    elif ok:
        assert payload == json.loads(str(detail["value"]))
