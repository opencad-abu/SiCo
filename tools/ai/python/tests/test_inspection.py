from __future__ import annotations

import json
from pathlib import Path

import pytest

from cadai.cdf_update import CDF_UPDATE_NAMES
from cadai.circuit_create import CREATE_NAMES
from cadai.circuit_library import LIBRARY_NAMES
from cadai.circuit_tools import CIRCUIT_TOOL_NAMES
from cadai.inspection import (
    MAX_INSPECT_ITEMS,
    InspectionArgumentError,
    build_read_only_skill,
    build_snapshot_skill,
    decode_read_only_result,
)
from cadai.launch import build_claude_command, build_codex_command, install_packaged_codex_skills
from cadai.mcp import TOOLS, McpServer
from cadai.pdk_schema import PDK_NAMES
from cadai.pdk_standard.tools import NAMES as STANDARD_PDK_NAMES
from cadai.simulation_recipe import SIMULATION_NAMES


class FakeClient:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.result = result or {"ok": True, "value": json.dumps({"ok": True, "items": []})}

    def call(self, method: str, arguments: dict[str, object]):
        self.calls.append((method, arguments))
        return True, self.result

    def close(self) -> None:
        pass


def test_read_only_skill_builder_uses_fixed_calls_and_escaped_strings() -> None:
    code = build_read_only_skill(
        "inspect_schematic",
        {
            "lib": 'my"lib',
            "cell": "top",
            "view_name": "schematic",
            "oa_view_type": "schematic",
            "include_placement": True,
            "max_items": 7,
        },
    )
    assert code == 'aiInspectSchematic("my\\"lib" "top" "schematic" "schematic" 7 t)'
    assert build_read_only_skill("inspect_schematic", {"max_items": 200}) == (
        "aiInspectSchematic(nil nil nil nil 200 nil)"
    )
    assert build_read_only_skill("inspect_layout", {}) == "aiInspectLayout(nil nil nil nil 50)"
    assert build_read_only_skill("inspect_library", {"name": "analogLib"}) == (
        'aiInspectLibrary("analogLib")'
    )
    assert build_read_only_skill(
        "inspect_config_binding",
        {"lib": "amsLDO", "cell": "LDO_MASTER", "view_name": "config", "max_items": 7},
    ) == 'aiInspectConfigBinding("amsLDO" "LDO_MASTER" "config" 7)'


def test_master_evidence_builder_batches_targets_and_defaults_the_view() -> None:
    assert build_read_only_skill(
        "inspect_master_evidence",
        {
            "targets": [
                {"lib": "analogLib", "cell": 'res"x'},
                {"lib": "smic28hkmg", "cell": "nch", "view_name": "schematicSymbol"},
            ],
            "max_items": 8,
        },
    ) == (
        'aiInspectMasterEvidence(list(list("analogLib" "res\\"x" "symbol") '
        'list("smic28hkmg" "nch" "schematicSymbol")) 8)'
    )
    assert build_read_only_skill(
        "inspect_master_evidence",
        {"targets": [{"lib": "SicoTest", "cell": "tb_inv_p3_g"}]},
    ) == 'aiInspectMasterEvidence(list(list("SicoTest" "tb_inv_p3_g" "symbol")) 50)'


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "between 1 and 8"),
        ({"targets": []}, "between 1 and 8"),
        ({"targets": [{"lib": "a", "cell": "b"}] * 9}, "between 1 and 8"),
        ({"targets": ["analogLib"]}, "must be an object"),
        ({"targets": [{"lib": "a"}]}, "cell is required"),
        ({"targets": [{"lib": "a", "cell": "b", "extra": 1}]}, "unsupported argument"),
    ],
)
def test_master_evidence_builder_rejects_unbounded_targets(arguments, message) -> None:
    with pytest.raises(InspectionArgumentError, match=message):
        build_read_only_skill("inspect_master_evidence", arguments)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"lib": "only-lib"}, "supplied together"),
        ({"view_name": "layout"}, "require lib and cell"),
        ({"max_items": 201}, "between 1 and 200"),
        ({"max_items": "100"}, "must be an integer"),
        ({"max_items": True}, "must be an integer"),
        ({"include_placement": True}, "unsupported argument"),
        ({"lib": "bad\nname", "cell": "top"}, "control character"),
        ({"unexpected": "value"}, "unsupported argument"),
    ],
)
def test_read_only_skill_builder_rejects_unbounded_or_ambiguous_inputs(
    arguments: dict[str, object], message: str
) -> None:
    with pytest.raises(InspectionArgumentError, match=message):
        build_read_only_skill("inspect_layout", arguments)


@pytest.mark.parametrize("diagnostic", [{}, dict(output="*WARNING* LIBRARY\n",
                        output_truncated=False, output_capture="skill_ports")])
def test_read_only_tool_routes_through_bounded_eval_and_decodes_json(diagnostic) -> None:
    client = FakeClient({"ok": True, "value": json.dumps({"ok": True, "libraries": ["basic"]}),
                         **diagnostic})
    response = McpServer(client).handle(
        {
            "jsonrpc": "2.0",
            "id": "libraries",
            "method": "tools/call",
            "params": {"name": "list_libraries", "arguments": {"max_items": 3}},
        }
    )
    assert client.calls == [("eval_skill", {"code": "aiListLibraries(3)"})]
    content = response["result"]["content"][0]["text"]
    assert json.loads(content) == {"ok": True, "libraries": ["basic"], **diagnostic}
    assert response["result"]["isError"] is False


def test_master_evidence_routes_through_bounded_eval() -> None:
    client = FakeClient(
        {"ok": True, "value": json.dumps({"ok": True, "kind": "master_evidence",
                                          "items": [], "count": 0})}
    )
    response = McpServer(client).handle(
        {
            "jsonrpc": "2.0",
            "id": "master-evidence",
            "method": "tools/call",
            "params": {
                "name": "inspect_master_evidence",
                "arguments": {"targets": [{"lib": "SicoTest", "cell": "tb_inv_p3_g"}]},
            },
        }
    )
    assert client.calls == [
        ("eval_skill",
         {"code": 'aiInspectMasterEvidence(list(list("SicoTest" "tb_inv_p3_g" "symbol")) 50)'})
    ]
    assert response["result"]["isError"] is False


@pytest.mark.parametrize("reply,code", [
    ({"value": '{"ok":false,"code":"target_changed"}'}, "target_changed"),
    ({"value": "{}", "value_truncated": True}, "result_too_large"),
    ({"spooled": True}, "result_too_large"),
    ({"value": "[1]"}, "invalid_result"),
    ({"value": "malformed"}, "invalid_result"),
    ({}, "invalid_result"),
])
def test_read_only_decode_retains_diagnostics_when_result_fails(reply, code):
    diagnostic = dict(output="PRINT\n*Error* READ\n", output_truncated=True,
                      output_capture="skill_ports")
    ok, result = decode_read_only_result({**reply, **diagnostic})
    assert not ok and result["code"] == code
    assert {key: result[key] for key in diagnostic} == diagnostic


def test_inspect_schematic_accepts_the_new_200_item_limit() -> None:
    client = FakeClient()
    response = McpServer(client).handle(
        {
            "jsonrpc": "2.0",
            "id": "schematic-200",
            "method": "tools/call",
            "params": {
                "name": "inspect_schematic",
                "arguments": {
                    "lib": "sxTest", "cell": "inv",
                    "view_name": "schematic", "oa_view_type": "schematic",
                    "max_items": 200,
                },
            },
        }
    )
    assert response["result"]["isError"] is False
    assert client.calls == [
        (
            "eval_skill",
            {"code": 'aiInspectSchematic("sxTest" "inv" "schematic" "schematic" 200 nil)'},
        )
    ]


def test_inspect_schematic_rejects_string_item_limit() -> None:
    client = FakeClient()
    response = McpServer(client).handle(
        {
            "jsonrpc": "2.0",
            "id": "schematic-string-limit",
            "method": "tools/call",
            "params": {
                "name": "inspect_schematic",
                "arguments": {"lib": "sxTest", "cell": "inv", "max_items": "100"},
            },
        }
    )
    assert response["error"]["code"] == -32602
    assert "max_items: expected integer" in response["error"]["message"]
    assert client.calls == []


def test_snapshot_skill_builder_requires_a_schematic_oa_view_type() -> None:
    assert build_snapshot_skill(
        {
            "lib": "work", "cell": "top", "view_name": "schematic",
            "oa_view_type": "schematic",
        },
        "/private/snapshot.jsonl",
    ) == (
        'aiSnapshotSchematic("work" "top" "schematic" "schematic" '
        '"/private/snapshot.jsonl" nil)'
    )
    with pytest.raises(InspectionArgumentError, match="requires oa_view_type schematic"):
        build_snapshot_skill(
            {
                "lib": "work",
                "cell": "top",
                "view_name": "layout",
                "oa_view_type": "maskLayout",
            },
            "/private/snapshot.jsonl",
        )


def test_read_only_tool_rejects_invalid_arguments_before_calling_virtuoso() -> None:
    client = FakeClient()
    response = McpServer(client).handle(
        {
            "jsonrpc": "2.0",
            "id": "bad",
            "method": "tools/call",
            "params": {"name": "inspect_library", "arguments": {"name": "a", "extra": 1}},
        }
    )
    assert response["error"]["code"] == -32602
    assert client.calls == []


def test_read_only_tools_are_advertised_as_safe_and_large_results_fail_closed() -> None:
    tools = {tool["name"]: tool for tool in TOOLS}
    for name in (
        "inspect_schematic",
        "inspect_layout",
        "inspect_symbol_ports",
        "inspect_master_evidence",
        "list_libraries",
        "inspect_library",
        "list_windows",
    ):
        assert tools[name]["annotations"]["readOnlyHint"] is True
        assert tools[name]["annotations"]["destructiveHint"] is False
        assert tools[name]["annotations"]["idempotentHint"] is True
        if "max_items" in tools[name]["inputSchema"]["properties"]:
            assert (
                tools[name]["inputSchema"]["properties"]["max_items"]["maximum"]
                == MAX_INSPECT_ITEMS
            )
    ok, error = decode_read_only_result({"ok": True, "value": "{}", "value_truncated": True})
    assert ok is False
    assert error["code"] == "result_too_large"


def test_agent_commands_allow_every_advertised_mcp_tool() -> None:
    tools = {tool["name"]: tool for tool in TOOLS}
    read_only = {
        name for name, tool in tools.items() if tool["annotations"]["readOnlyHint"] is True
    }
    mutating = set(tools) - read_only
    # Registrations grow independently; verify uniqueness and policy for every tool.
    assert len(tools) == len(TOOLS)
    assert read_only and mutating
    assert read_only.isdisjoint(mutating)
    for name, tool in tools.items():
        annotations = tool["annotations"]
        if name == "execute_circuit_edit":
            assert annotations["destructiveHint"] is True
        elif name in (CIRCUIT_TOOL_NAMES | CREATE_NAMES | SIMULATION_NAMES
                      | STANDARD_PDK_NAMES | LIBRARY_NAMES | CDF_UPDATE_NAMES) | {
            "run_aivw_recipe", "submit_candidate", "extract_circuit_templates",
            "project_circuit_template", "preview_template_symbol",
            "create_template_symbol", "inspect_template_symbol",
            "preview_template_placement", "draw_symbol", "probe_cdf_parameters",
            "execute_circuit_operation", "init_pdk",
        }:
            assert annotations["destructiveHint"] is False
        else:
            assert annotations["destructiveHint"] is (name in mutating)
        # PDK lifecycle tools own their own idempotency exceptions.
        if name == "init_pdk":
            assert annotations["idempotentHint"] is True
        elif name not in PDK_NAMES:
            assert annotations["idempotentHint"] is (name in read_only)
        if name in read_only:
            assert annotations["openWorldHint"] is False

    codex = build_codex_command(
        "/bin/codex", "/bin/python3", Path("/work"), Path("/cad/python"), 300, environment={}
    )
    configs = [codex[index + 1] for index, item in enumerate(codex[:-1]) if item == "-c"]
    enabled_config = next(
        item for item in configs if item.startswith("mcp_servers.sico_ic.enabled_tools=")
    )
    assert set(json.loads(enabled_config.partition("=")[2])) == set(tools)
    approval_prefix = "mcp_servers.sico_ic.tools."
    approval_suffix = ".approval_mode"
    approvals = {}
    for config in configs:
        setting, separator, value = config.partition("=")
        if separator and setting.startswith(approval_prefix) and setting.endswith(approval_suffix):
            name = setting[len(approval_prefix) : -len(approval_suffix)]
            approvals[name] = json.loads(value)
    # Packaged Codex 0.114 uses enabled_tools; per-tool approval_mode is unsupported.
    assert approvals == {}

    claude = build_claude_command("/bin/claude", Path("/tmp/mcp.json"), environment={})
    prefix = "mcp__sico_ic__"
    allowed_option = next(item for item in claude if item.startswith("--allowedTools="))
    allowed = {
        item.removeprefix(prefix)
        for item in allowed_option.partition("=")[2].split(",")
        if item
    }
    assert allowed == set(tools)
    assert "--settings" not in claude


def test_packaged_codex_skills_are_linked_without_overwriting_user_content(tmp_path: Path) -> None:
    release = tmp_path / "release"
    (release / "skills" / "virtuoso-assistant").mkdir(parents=True)
    home = tmp_path / "codex-home"
    home.mkdir(mode=0o700)

    install_packaged_codex_skills(home, release)

    link = home / "skills" / "sico-cad-ai"
    assert link.is_symlink()
    assert link.resolve() == (release / "skills").resolve()
    (home / "skills" / "unrelated").mkdir()
    install_packaged_codex_skills(home, release)
    assert (home / "skills" / "unrelated").is_dir()
