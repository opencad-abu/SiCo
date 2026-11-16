"""Creation request validation before native effects and exact digest transport."""

import copy
import json

import pytest
from circuit_geometry_fixture import sample_geometry

from cadai.circuit_create import CREATE_TOOLS, build_create_call, call_create, payload
from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_schema import CircuitSpecError
from cadai.mcp import McpServer
from cadai.skill_check import check_source


def args():
    result = sample_geometry()
    return {
        **result,
        "geometry_plan_digest": preview_geometry(**result)["geometry_plan_digest"],
        "task_ref": "task:unit",
        "request_id": "unit",
    }


def test_prepare_is_one_bounded_checked_expression_and_input_unchanged():
    data = args()
    before = copy.deepcopy(data)
    code = build_create_call("prepare_circuit_creation", data)
    assert code.startswith('aiCreatePrepare("task:unit" "unit" ')
    assert "I_filter_0" in code and '"RLOAD"' in code
    assert check_source(code.encode())["ok"]
    assert data == before


@pytest.mark.parametrize(
    "update",
    [
        {"geometry_plan_digest": "0" * 64},
        {"preview_digest": "0" * 64},
        {"task_ref": "bad/ref"},
        {"request_id": "bad\nrequest"},
        {"overwrite": True},
    ],
)
def test_bad_requests_rejected_before_call(update):
    with pytest.raises(CircuitSpecError):
        build_create_call("prepare_circuit_creation", {**args(), **update})


def test_schematic_dut_is_rejected_before_native_preparation():
    class Client:
        def call(self, *unused):
            raise AssertionError("invalid DUT must not reach Virtuoso")

    data = args()
    data["bindings"]["masters"][-1]["target"]["view"] = "schematic"
    with pytest.raises(CircuitSpecError, match="DUT requires a design symbol view"):
        call_create("prepare_circuit_creation", data, Client())


def test_target_inspection_is_read_only_and_never_creates_a_decision():
    class Client:
        def call(self, _method, arguments):
            assert "aiCrInspectTarget" in arguments["code"]
            assert arguments["code"].startswith("aiCrInspectTarget(nil ")
            return True, {"value": json.dumps({
                "ok": True,
                "target_ref": "circuit-target:observed",
                "target": ["Work", "Cell", "schematic"],
                "content_state": "nonempty",
                "next_action": "prepare_circuit_creation",
            })}

    ok, result = call_create("inspect_circuit_target", {
        "request_id": "inspect",
        "target": {"library": "Work", "cell": "Cell", "view": "schematic"},
    }, Client())
    assert ok and "decision_required" not in result
    assert result["next_action"] == "prepare_circuit_creation"


def test_no_arbitrary_code_in_string_parameters():
    data = args()
    data["spec"]["instances"][0]["parameters"]["value"] = '") exit() ("'
    from cadai.circuit_spec_plan import preview_circuit
    from cadai.circuit_spec_schema import digest

    data["geometry"]["instances"][0]["parameters_digest"] = digest(
        data["spec"]["instances"][0]["parameters"]
    )
    data["preview_digest"] = preview_circuit(data["spec"], data["bindings"])["preview_digest"]
    geo_args = {k: data[k] for k in ("spec", "bindings", "preview_digest", "geometry", "layout")}
    data["geometry_plan_digest"] = preview_geometry(**geo_args)["geometry_plan_digest"]
    code = build_create_call("prepare_circuit_creation", data)
    assert '\\"' in code and check_source(code.encode())["ok"]


def test_native_route_and_failure_preserved():
    class Client:
        def __init__(self):
            self.calls = []

        def call(self, name, args):
            self.calls.append((name, args))
            return True, {
                "ok": True,
                "value": json.dumps({"ok": False, "code": "prepare_not_found"}),
            }

    client = Client()
    request = {
        "prepare_ref": "create-prepare:x",
        "geometry_plan_digest": "a" * 64,
        "request_id": "x",
    }
    ok, result = call_create("create_circuit_from_plan", request, client)
    assert not ok and result["code"] == "prepare_not_found"
    assert client.calls[0][0] == "eval_skill_native"
    response = McpServer(client).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "create_circuit_from_plan", "arguments": request},
        }
    )
    assert response["result"]["isError"]


def test_creation_tool_annotations_and_status_refs():
    tools = {t["name"]: t for t in CREATE_TOOLS}
    assert tools["prepare_circuit_creation"]["annotations"]["readOnlyHint"]
    assert not tools["create_circuit_from_plan"]["annotations"]["readOnlyHint"]
    assert tools["inspect_created_circuit"]["annotations"]["readOnlyHint"]
    assert (
        build_create_call("inspect_created_circuit", {"circuit_ref": "created:x"})
        == 'aiCreateInspect("created:x")'
    )
    assert (
        build_create_call("release_created_circuit", {"circuit_ref": "created:x"})
        == 'aiCreateRelease("created:x")'
    )


@pytest.mark.parametrize("same_net", [True, False])
def test_coincident_pins_draw_once_only_after_same_net_geometry_validation(same_net):
    from cadai.circuit_spec_plan import preview_circuit

    data = sample_geometry()
    device = next(i for i in data["spec"]["instances"] if i["id"] == "dut")
    device["name"] = "I_filter_0"
    master = next(m for m in data["bindings"]["masters"] if m["id"] == device["master"])
    geometry = next(g for g in data["geometry"]["instances"] if g["instance"] == device["id"])
    terminal = geometry["terminals"][-1]["name"]
    master["terminals"].append(dict(master["terminals"][-1], name="COINCIDENT"))
    geometry["terminals"].append(dict(copy.deepcopy(geometry["terminals"][-1]), name="COINCIDENT"))
    device["connections"]["COINCIDENT"] = (
        device["connections"][terminal] if same_net else device["connections"]["IN"]
    )
    preview = preview_circuit(data["spec"], data["bindings"])
    data["preview_digest"] = preview["preview_digest"]
    data["geometry"]["binding_digest"] = preview["plan"]["binding_digest"]
    result = preview_geometry(**data)
    data["geometry_plan_digest"] = result["geometry_plan_digest"]
    if same_net:
        native = payload(data)
        assert len(native[3]) == len(result["plan"]["wires"]) - 1
        row = next(row for row in native[1] if row[0] == device["name"])
        assert dict(row[6])["COINCIDENT"] == device["connections"][terminal]
    else:
        assert not result["plan"]["geometry_checks_passed"]
        with pytest.raises(CircuitSpecError, match="geometry conflicts"):
            payload(data)
