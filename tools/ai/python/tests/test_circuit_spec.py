"""Generic creation-plan invariants, project isolation, and precise missing data."""

import copy
import json

import pytest
from circuit_spec_fixture import sample

from cadai.circuit_create import payload
from cadai.circuit_generic import GENERIC_TOOLS, checked_preview
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import CircuitSpecError, digest, validate
from cadai.mcp import McpServer


def test_two_project_bindings_same_public_entrypoint():
    for project, cell in [("a", "filter"), ("b", "comparator")]:
        spec, bindings = sample(project)
        before = copy.deepcopy((spec, bindings))
        out = preview_circuit(spec, bindings)
        assert out["ok"] and out["status"] == "structural_preview"
        plan = out["plan"]
        assert out["preview_digest"] == digest(plan)
        assert plan["binding_complete"] and plan["structural_valid"]
        assert not plan["creation_ready"] and not plan["live_binding_verified"]
        assert not plan["simulation_qualified"]
        assert (spec, bindings) == before
        placed = {r["id"]: r for r in plan["placement"]["instances"]}
        assert placed["dut"]["name"] == "I_" + cell + "_0"
        assert [placed[k]["column"] for k in ("stim", "dut", "load")] == ["left", "center", "right"]
        assert len(plan["placement"]["stubs"]) == 7
        assert plan["placement"]["notes"][0]["text"] == "负载区"
        assert not plan["placement"]["geometry_verified"]


def test_multiple_duts_and_stable_explicit_name_reservation():
    spec, bindings = sample()
    other = copy.deepcopy(spec["instances"][-1])
    other.update(id="dut_2", name="I_filter_0")
    spec["instances"].append(other)
    first = preview_circuit(spec, bindings)
    assert {i["id"]: i["name"] for i in first["plan"]["spec"]["instances"]}["dut"] == "I_filter_1"
    for key in ("instances", "nets", "ports"):
        spec[key].reverse()
    assert preview_circuit(spec, bindings)["preview_digest"] == first["preview_digest"]


def test_single_endpoint_nets_block_geometry_and_creation():
    spec, bindings = sample()
    dut = next(row for row in spec["instances"] if row["id"] == "dut")
    spec["instances"] = [dut]
    spec["nets"] = [
        {"name": "IN", "scope": "local"},
        {"name": "OUT", "scope": "local"},
    ]
    spec["ports"] = []
    spec["notes"] = []
    dut["connections"] = {"IN": "IN", "OUT": "OUT", "VSS": "OUT"}
    bindings["masters"] = [
        row for row in bindings["masters"] if row["id"] == "selected_dut"
    ]

    preview = preview_circuit(spec, bindings)

    assert preview["next_action"] == "resolve_floating_nets"
    assert preview["geometry_preview_ready"] is False
    assert preview["blocking_warnings"] == [{"code": "single_endpoint_net", "net": "IN"}]
    with pytest.raises(CircuitSpecError, match="only one endpoint"):
        payload({"spec": spec, "bindings": bindings})


def test_discovery_callback_status_points_to_binding_refs():
    spec, bindings = sample()
    bindings["masters"][0]["callbacks"] = {"status": "known", "order": ["value"]}
    with pytest.raises(CircuitSpecError, match="binding_refs from bind_pdk_device"):
        preview_circuit(spec, bindings)


@pytest.mark.parametrize("view,kind", [("schematic", "design"), ("layout", "design"),
                                      ("symbol", "device")])
def test_tb_rejects_non_symbol_or_flattened_device_dut(view, kind):
    spec, bindings = sample()
    bindings["masters"][-1]["target"]["view"] = view
    bindings["masters"][-1]["kind"] = kind
    with pytest.raises(CircuitSpecError, match="DUT requires a design symbol view"):
        preview_circuit(spec, bindings)


def test_ports_order_notes_and_explicit_open_terminal():
    spec, bindings = sample()
    bindings["masters"][-1]["terminals"].append({"name": "NC", "direction": "output"})
    spec["instances"][-1]["unconnected"] = {"NC": "diagnostic output unused"}
    spec["ports"] = [
        {"name": "gnd!", "net": "gnd!", "direction": "inputOutput"},
        {"name": "IN", "net": "IN", "direction": "input"},
        {"name": "OUT", "net": "OUT", "direction": "output"},
    ]
    plan = preview_circuit(spec, bindings)["plan"]
    assert plan["binding_complete"]
    assert {p["name"]: p["region"] for p in plan["placement"]["ports"]} == {
        "gnd!": "upper_left",
        "IN": "lower_left",
        "OUT": "right",
    }
    assert plan["warnings"][0]["code"] == "intentional_unconnected"
    assert all(r["endpoint"].get("terminal") != "NC" for r in plan["placement"]["stubs"])


@pytest.mark.parametrize(
    "case,code",
    [
        ("missing_master", "master_binding_missing"),
        ("terminals", "terminal_metadata_incomplete"),
        ("parameters", "parameter_metadata_incomplete"),
        ("callback", "callback_policy_unknown"),
        ("extension", "callback_extension_not_validated"),
        ("unassigned", "terminal_unassigned"),
    ],
)
def test_missing_metadata_reported_without_fabricated_defaults(case, code):
    spec, bindings = sample()
    if case == "missing_master":
        bindings["masters"].pop(0)
    elif case in {"terminals", "parameters"}:
        bindings["masters"][0][case + "_complete"] = False
    elif case == "callback":
        bindings["masters"][0]["callbacks"] = {"status": "unknown"}
    elif case == "extension":
        bindings["masters"][0]["callbacks"] = {
            "status": "required",
            "extension_ref": "project:extension:v1",
        }
    else:
        del spec["instances"][0]["connections"]["N"]
    out = preview_circuit(spec, bindings)
    assert out["status"] == "binding_incomplete"
    assert code in {i["code"] for i in out["plan"]["issues"]}
    assert not out["plan"]["creation_ready"]


@pytest.mark.parametrize(
    "case",
    [
        "project",
        "snapshot",
        "duplicate_id",
        "duplicate_net",
        "duplicate_name",
        "bad_name",
        "missing_dut",
        "ambiguous_role",
        "unknown_parameter",
        "unknown_terminal",
        "boolean_as_number",
        "bad_choice",
        "locked_parameter",
        "undeclared_net",
        "double_terminal",
        "bus_bundle",
        "unknown_field",
        "bad_note",
        "unused_net",
        "global_scope",
        "alias_port",
        "nonfinite",
    ],
)
def test_invalid_specs_fail_before_any_creation(case):
    spec, bindings = sample()
    inst, master = spec["instances"][0], bindings["masters"][0]
    if case == "project":
        bindings["project_ref"] = "another_project"
    elif case == "snapshot":
        bindings["snapshot_ref"] = "stale"
    elif case == "duplicate_id":
        spec["instances"].append(copy.deepcopy(inst))
    elif case == "duplicate_net":
        spec["nets"].append(copy.deepcopy(spec["nets"][0]))
    elif case == "duplicate_name":
        spec["instances"][1]["name"] = "Rout"
    elif case == "bad_name":
        spec["instances"][-1]["name"] = "X1"
    elif case == "missing_dut":
        spec["instances"][-1]["role"] = "auxiliary"
    elif case == "ambiguous_role":
        inst["role"] = "device"
    elif case == "unknown_parameter":
        inst["parameters"] = {"guessedW": "1u"}
    elif case == "unknown_terminal":
        inst["connections"]["guessedBulk"] = "gnd!"
    elif case == "boolean_as_number":
        master["parameters"][0]["type"] = "number"
        inst["parameters"]["value"] = True
    elif case == "bad_choice":
        master["parameters"][0]["choices"] = ["100", "200"]
    elif case == "locked_parameter":
        master["parameters"][0]["editable"] = False
    elif case == "undeclared_net":
        inst["connections"]["P"] = "typo"
    elif case == "double_terminal":
        inst["unconnected"]["P"] = "intentional"
    elif case == "bus_bundle":
        spec["nets"][0]["name"] = "DATA<7:0>"
    elif case == "unknown_field":
        spec["skill"] = "exit()"
    elif case == "bad_note":
        spec["notes"][0]["instances"] = ["nonexistent"]
    elif case == "unused_net":
        spec["nets"].append({"name": "empty", "scope": "local"})
    elif case == "global_scope":
        spec["nets"][0]["scope"] = "global"
    elif case == "alias_port":
        spec["ports"] = [{"name": "ALIAS", "direction": "output", "net": "OUT"}]
    elif case == "nonfinite":
        inst["parameters"]["value"] = float("inf")
    with pytest.raises(CircuitSpecError):
        preview_circuit(spec, bindings)


def test_parameter_strings_preserved_no_evaluation_or_conversions():
    spec, bindings = sample()
    value = 'RLOAD * 2 + "quoted"'
    spec["instances"][0]["parameters"]["value"] = value
    plan = preview_circuit(spec, bindings)["plan"]
    assert next(i for i in plan["spec"]["instances"] if i["id"] == "load")["parameters"] == {
        "value": value
    }


def test_circuit_without_dut_and_semantically_changed_bindings():
    spec, bindings = sample()
    spec["kind"] = "circuit"
    for instance in spec["instances"]:
        instance["role"] = "device"
    first = preview_circuit(spec, bindings)
    assert {p["column"] for p in first["plan"]["placement"]["instances"]} == {"template_or_user"}
    bindings["masters"][0]["revision"] = "rev9"
    second = preview_circuit(spec, bindings)
    assert first["preview_digest"] != second["preview_digest"]
    assert first["plan"]["binding_digest"] != second["plan"]["binding_digest"]


def test_mcp_tool_schema_route_and_no_live_calls():
    class Client:
        def call(self, *args):
            raise AssertionError("preview must not access Virtuoso")

    spec, bindings = sample()
    args = {"spec": spec, "bindings": bindings}
    validate(args, GENERIC_TOOLS[0]["inputSchema"])
    server = McpServer(Client())
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "preview_circuit_spec", "arguments": args},
    }
    result = server.handle(request)["result"]
    assert not result["isError"]
    assert json.loads(result["content"][0]["text"])["plan"]["binding_complete"]
    request["params"]["arguments"]["bindings"]["project_ref"] = "wrong"
    assert server.handle(request)["error"]["code"] == -32602
    try:
        verification = McpServer(Client(), profile="verification")
    except TypeError:
        return  # Isolated committed HEAD has no optional verification profile module.
    assert verification.handle(request)["error"]["code"] == -32602


def test_missing_top_level_fields_and_schema_version():
    with pytest.raises(CircuitSpecError):
        checked_preview({})
    spec, bindings = sample()
    spec["schema"] = "future"
    with pytest.raises(CircuitSpecError):
        preview_circuit(spec, bindings)


def test_unresolved_design_master_defers_name_and_never_guesses_cell():
    spec, bindings = sample()
    bindings["masters"].pop()
    result = preview_circuit(spec, bindings)
    assert result["status"] == "binding_incomplete"
    dut = next(p for p in result["plan"]["placement"]["instances"] if p["id"] == "dut")
    assert dut["name"] is None
    assert "instance_name_unresolved" in {p["code"] for p in result["plan"]["issues"]}


@pytest.mark.parametrize("case", ["instances", "bytes", "huge_integer", "nested_value"])
def test_bounded_contract_rejects_oversized_or_non_scalar_input(case):
    spec, bindings = sample()
    if case == "instances":
        spec["instances"] *= 22
    elif case == "bytes":
        spec["instances"][0]["parameters"]["value"] = "x" * 200000
    elif case == "huge_integer":
        spec["instances"][0]["parameters"]["value"] = 10**500
    else:
        spec["instances"][0]["parameters"]["value"] = {"eval": "unsupported"}
    with pytest.raises(CircuitSpecError):
        preview_circuit(spec, bindings)


@pytest.mark.parametrize("case", ["library_paths", "master_metadata", "invalid_unicode"])
def test_conflicting_project_evidence_and_invalid_unicode_rejected(case):
    spec, bindings = sample()
    if case == "library_paths":
        bindings["masters"][1]["target"]["library"] = bindings["masters"][0]["target"]["library"]
    elif case == "master_metadata":
        extra = copy.deepcopy(bindings["masters"][0])
        extra.update(id="another_alias", revision="different")
        bindings["masters"].append(extra)
    else:
        spec["notes"][0]["text"] = "\ud800"
    with pytest.raises(CircuitSpecError):
        preview_circuit(spec, bindings)


def test_tool_preview_page_drops_request_echoes_inside_the_result_budget():
    """工具结果只带权威增量：请求回显不再把预览推出 8,000 字符截断线。"""
    from cadai.circuit_generic import call_generic

    spec, bindings = sample()
    full = preview_circuit(spec, bindings)["plan"]
    page = call_generic("preview_circuit_spec", {"spec": spec, "bindings": bindings})
    full_bytes = len(json.dumps(full, ensure_ascii=False))
    page_bytes = len(json.dumps(page["plan"], ensure_ascii=False))
    # 夹具规模小，固定差值不适用；契约是"回显不再出现 + 稳定小于完整 plan"。
    assert page_bytes < full_bytes
    assert len(json.dumps(page, ensure_ascii=False)) <= 8_000
    assert page["plan"]["spec"]["status"] == "request_echo_omitted"
    assert set(page["plan"]["spec"]["omitted"]) == {"instances", "nets", "ports"}
    assert page["plan"]["spec"]["binding_snapshot"] == full["spec"]["binding_snapshot"]
    assert page["plan"]["spec"]["digest"] == digest(full["spec"])
    assert page["plan"]["bindings"]["digest"] == digest(full["bindings"])
    revisions = [row["revision"] for row in page["plan"]["bindings"]["masters"]]
    assert revisions == [row["revision"] for row in full["bindings"]["masters"]]
    assert "parameters" not in page["plan"]["bindings"]["masters"][0]
    # 内部路径（模板适配等）继续拿到完整回显。
    assert "instances" in full["spec"] and "masters" in full["bindings"]
