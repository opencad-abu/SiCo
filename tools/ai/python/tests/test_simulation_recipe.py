"""Explicit recipe validation and native transport boundaries."""

import copy
import json

import pytest
from simulation_recipe_fixture import recipe

from cadai.circuit_spec_schema import CircuitSpecError
from cadai.mcp import McpServer
from cadai.simulation_recipe import (
    call_simulation,
    model_identities,
    native_recipe,
    preview_recipe,
    with_view_defaults,
)
from cadai.skill_check import check_source


def test_preview_explicit_multi_test_values_without_live_calls():
    data = recipe()
    before = copy.deepcopy(data)
    result = preview_recipe(data)
    assert result["expected_test_points"] == [
        dict(test=t, corner="nominal", point=1) for t in ("settling", "bias")
    ]
    assert not result["live_verified"] and not result["simulation_qualified"]
    assert native_recipe(data)[3][0][3] == [["RVAL", "4K"]]
    assert data == before


def test_ac_decade_count_activates_ade_step_selectors():
    data = recipe()
    data["tests"][0]["analyses"] = [
        dict(name="ac", options=dict(start="100", stop="10M", dec="80"))
    ]
    assert preview_recipe(data)["ok"]
    options = dict(native_recipe(data)[3][0][5][0][1])
    assert options["incrType"] == "Logarithmic"
    assert options["stepTypeLog"] == "Points Per Decade" and options["dec"] == "80"
    data["tests"][0]["analyses"][0]["options"]["lin"] = "20"
    with pytest.raises(CircuitSpecError, match="single frequency step"):
        preview_recipe(data)


def test_unselected_expression_warning_preserves_explicit_recipe():
    data = recipe()
    data["tests"][0]["name"] = "probe_y"
    data["tests"][0]["outputs"] = [
        dict(name="DESELECTED", kind="expression", value='VDC("/Y")', plot=False, save=True),
        dict(name="SELECTED", kind="expression", value='VDC("/Y")', plot=True, save=True),
        dict(name="SAVED_NET", kind="signal", value="/Y", plot=False, save=True),
    ]
    before = copy.deepcopy(data)
    preview = preview_recipe(data)
    assert [(row["code"], row["test"], row["output"]) for row in preview["warnings"]] == [
        ("expression_not_selected", "probe_y", "DESELECTED")
    ]
    assert preview["ok"] and data == before
    assert native_recipe(data)[3][0][6][0][3:] == [False, True]
    assert preview_recipe(recipe())["warnings"] == []


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_test",
        "duplicate_output",
        "bad_analysis",
        "bad_option",
        "missing_stop",
        "wrong_boolean",
        "sweep",
        "range_sweep",
        "bracket_sweep",
        "reserved_temperature",
        "auto_temperature",
        "bad_signal",
        "bad_simulator_option",
        "bad_simulator_value",
    ],
)
def test_recipe_rejects_unsupported_or_ambiguous_inputs(change):
    data = recipe()
    test = data["tests"][0]
    if change == "duplicate_test":
        data["tests"].append(copy.deepcopy(test))
    elif change == "duplicate_output":
        test["outputs"].append(copy.deepcopy(test["outputs"][0]))
    elif change == "bad_analysis":
        test["analyses"][0]["name"] = "pss"
    elif change == "bad_option":
        test["analyses"][0]["options"]["exec"] = "run()"
    elif change == "missing_stop":
        test["analyses"][0]["options"].pop("stop")
    elif change == "wrong_boolean":
        data["tests"][1]["analyses"][0]["options"]["saveOppoint"] = "yes"
    elif change == "sweep":
        test["variables"]["RVAL"] = "1K 2K"
    elif change == "range_sweep":
        test["variables"]["RVAL"] = "1K:1K:4K"
    elif change == "bracket_sweep":
        test["variables"]["RVAL"] = "[1K,2K]"
    elif change == "reserved_temperature":
        data["variables"]["temperature"] = "27"
    elif change == "auto_temperature":
        data.pop("temperature_c")
    elif change == "bad_signal":
        test["outputs"][1]["value"] = "OUT"
    elif change == "bad_simulator_option":
        test["simulator_options"] = {"mystery": 1}
    elif change == "bad_simulator_value":
        test["simulator_options"] = {"scale": "big"}
    with pytest.raises(CircuitSpecError):
        preview_recipe(data)


def test_registered_simulator_options_flow_into_the_native_payload():
    data = recipe()
    data["tests"][0]["simulator_options"] = {"scale": 0.9}
    preview = preview_recipe(data)
    assert preview["recipe_digest"] and preview["warnings"] == []
    first = native_recipe(data)[3][0]
    assert first[9] == [["scale", "0.9"]] and len(first) == 10
    assert len(native_recipe(recipe())[3][0]) == 9
    data["tests"][0]["config_ref"] = "config:my_config"
    both = native_recipe(data)[3][0]
    assert both[9] == "config:my_config" and both[10] == [["scale", "0.9"]] and len(both) == 11
    with pytest.raises(CircuitSpecError, match="unsupported simulator option"):
        preview_recipe({**data, "tests": [{**data["tests"][0], "simulator_options": {"nope": 1}}]})
    with pytest.raises(CircuitSpecError, match="invalid value for simulator option"):
        preview_recipe({**data, "tests": [{**data["tests"][0], "simulator_options": {"scale": 0}}]})


def test_omitted_view_lists_default_to_the_project_list():
    """内置器件只有 symbol/spectre 视图；缺省视图表不得让网表在 maestro 里才失败。"""
    data = recipe()
    for test in data["tests"]:
        del test["switch_views"]
        del test["stop_views"]
    before = copy.deepcopy(data)
    preview = preview_recipe(data)
    assert data == before, "preview must not fill defaults in the caller's recipe"
    assert [t["switch_views"] for t in preview["recipe"]["tests"]] == [
        ["spectre", "schematic"], ["spectre", "schematic"]
    ]
    assert [t["stop_views"] for t in preview["recipe"]["tests"]] == [["spectre"], ["spectre"]]
    assert preview["recipe_digest"] == preview_recipe(with_view_defaults(data))["recipe_digest"]
    rows = native_recipe(with_view_defaults(data))[3]
    assert [row[7] for row in rows] == [["spectre", "schematic"]] * 2
    assert [row[8] for row in rows] == [["spectre"]] * 2


def test_explicit_view_lists_are_preserved_and_design_view_required():
    data = recipe()
    data["tests"][1]["switch_views"] = ["cmos_sch", "schematic"]
    data["tests"][1]["stop_views"] = ["cmos_sch"]
    preview = preview_recipe(data)
    assert preview["recipe"]["tests"][1]["switch_views"] == ["cmos_sch", "schematic"]
    assert preview["recipe"]["tests"][1]["stop_views"] == ["cmos_sch"]
    missing = copy.deepcopy(data)
    missing["tests"][0]["switch_views"] = ["spectre"]
    with pytest.raises(CircuitSpecError, match="must include the test design view"):
        preview_recipe(missing)
    config = copy.deepcopy(missing)
    config["tests"][0]["config_ref"] = "config:my_config"
    assert preview_recipe(config)["ok"]


def test_create_normalizes_the_same_recipe_without_view_lists():
    class Client:
        def __init__(self):
            self.calls = []

        def call(self, name, args):
            self.calls.append((name, args))
            return True, {"ok": True, "value": json.dumps({"ok": True, "ref": "simulation:create"})}

    client = Client()
    data = recipe()
    for test in data["tests"]:
        del test["switch_views"]
        del test["stop_views"]
    args = dict(recipe=data, recipe_digest=preview_recipe(data)["recipe_digest"],
                task_ref="task:test", request_id="create")
    ok, _reply = call_simulation("create_simulation_setup", args, client)
    assert ok and "aiSimCreate(" in client.calls[0][1]["code"]
    assert '"spectre"' in client.calls[0][1]["code"] and '"schematic"' in client.calls[0][1]["code"]


def test_model_content_identity_and_missing_file(tmp_path):
    path = tmp_path / "m.scs"
    path.write_text("section tt\nendsection tt\n")
    data = recipe(model=path)
    first = model_identities(data)
    assert len(first) == 1
    path.write_text("section ss\nendsection ss\n")
    assert first != model_identities(data)
    with pytest.raises(CircuitSpecError):
        model_identities(recipe(model=tmp_path / "absent"))


@pytest.mark.parametrize("kind", ["net", "terminal", "terminalV"])
def test_signal_path_structure_and_hierarchy_boundary(kind):
    data = recipe()
    output = dict(name="probe", kind="signal", signal_type=kind, plot=True, save=True)
    data["tests"][0]["outputs"] = [output]
    leaf = "/OUT" if kind == "net" else "/V1/PLUS"
    for prefix in ("", "/X" * 8):
        output["value"] = prefix + leaf
        assert preview_recipe(data)["ok"]
    for bad in ("/", "//OUT", "/X//OUT", "/X/OUT/", "OUT", "/X" * 9 + leaf):
        output["value"] = bad
        with pytest.raises(CircuitSpecError, match="absolute path"):
            preview_recipe(data)
    if kind != "net":
        output["value"] = "/PLUS"
        with pytest.raises(CircuitSpecError, match="instance/terminal"):
            preview_recipe(data)


def test_native_request_quotes_and_digest_gate():
    class Client:
        def __init__(self):
            self.calls = []

        def call(self, name, args):
            self.calls.append((name, args))
            return True, {"ok": True, "value": json.dumps({"ok": False, "code": "test_boundary"})}

    client = Client()
    data = recipe()
    data["tests"][0]["outputs"][0]["value"] = 'value(VT("/OUT") 1e-6)'
    args = dict(
        recipe=data,
        recipe_digest=preview_recipe(data)["recipe_digest"],
        task_ref="task:test",
        request_id="create",
    )
    ok, reply = call_simulation("create_simulation_setup", args, client)
    assert not ok and reply["code"] == "test_boundary"
    code = client.calls[0][1]["code"]
    assert (
        code.startswith("aiSimCreate(")
        and '\\"/OUT\\"' in code
        and check_source(code.encode())["ok"]
    )
    args["recipe_digest"] = "0" * 64
    with pytest.raises(CircuitSpecError):
        call_simulation("create_simulation_setup", args, client)
    assert len(client.calls) == 1


def test_preview_routes_without_a_live_client():
    class Client:
        def call(self, *args):
            raise AssertionError("preview must remain local")

    response = McpServer(Client()).handle(
        dict(
            jsonrpc="2.0",
            id=1,
            method="tools/call",
            params=dict(name="preview_simulation_recipe", arguments={"recipe": recipe()}),
        )
    )
    assert not response["result"].get("isError")
    assert json.loads(response["result"]["content"][0]["text"])["recipe_digest"]
