"""Geometric plans cannot change connectivity or silently select/round pin coordinates."""

import copy
import json

import pytest
from circuit_geometry_fixture import sample_geometry as routed_sample_geometry

from cadai.circuit_generic import GENERIC_TOOLS, call_generic
from cadai.circuit_geometry import preview_geometry
from cadai.circuit_geometry_schema import MATRICES, transform, transform_box
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import CircuitSpecError, digest, validate
from cadai.mcp import McpServer


def sample_geometry():
    # These assertions describe labelled stubs. Default Cadence routing has its
    # own endpoint/coverage tests in test_circuit_geometry_routing.py.
    args = routed_sample_geometry()
    args["layout"]["routing"] = {"mode": "stub"}
    return args


def test_explicit_geometry_resolves_every_net_endpoint_and_preserves_inputs():
    args = sample_geometry()
    before = copy.deepcopy(args)
    out = preview_geometry(**args)
    plan = out["plan"]
    assert args == before
    assert out["status"] == "geometry_checked" and not plan["issues"]
    assert out["geometry_plan_digest"] == digest(plan)
    assert not plan["creation_ready"] and not plan["live_binding_verified"]
    assert not plan["label_rendering_verified"]
    assert len(plan["wires"]) == 7
    for wire in plan["wires"]:
        endpoint = wire["endpoint"]
        inst = next(i for i in args["spec"]["instances"] if i["id"] == endpoint["instance"])
        assert wire["net"] == inst["connections"][endpoint["terminal"]]
        assert wire["label"]["text"] == wire["net"]
    assert plan["notes"][0]["bbox"] == [[3.5, -0.5], [4.5, 0.5]]


@pytest.mark.parametrize("orientation", MATRICES)
def test_all_orientations_keep_anchor_and_escape_consistent(orientation):
    args = sample_geometry()
    args["layout"]["instances"]["stim"]["orientation"] = orientation
    out = preview_geometry(**args)
    assert out["plan"]["geometry_checks_passed"]
    wire = next(
        w for w in out["plan"]["wires"] if w["endpoint"] == {"instance": "stim", "terminal": "P"}
    )
    assert wire["points"] == [
        transform([0, 0.5], orientation, [-4, 0]),
        transform([0, 0.75], orientation, [-4, 0]),
    ]


@pytest.mark.parametrize(
    "case",
    [
        "preview",
        "project",
        "snapshot",
        "binding",
        "revision",
        "parameters",
        "missing_instance",
        "missing_terminal",
        "unknown_choice",
        "multiple",
        "off_grid",
        "inside_pin",
        "inward_escape",
        "zero_box",
    ],
)
def test_reject_stale_incomplete_or_ambiguous_geometry(case):
    args = sample_geometry()
    geo = args["geometry"]
    row = geo["instances"][0]
    pin = row["terminals"][0]["anchors"][0]
    if case == "preview":
        args["preview_digest"] = "stale"
    elif case == "project":
        geo["project_ref"] = "wrong"
    elif case == "snapshot":
        geo["binding_snapshot"] = "wrong"
    elif case == "binding":
        geo["binding_digest"] = "wrong"
    elif case == "revision":
        row["master_revision"] = "changed"
    elif case == "parameters":
        row["parameters_digest"] = "changed"
    elif case == "missing_instance":
        geo["instances"].pop()
    elif case == "missing_terminal":
        row["terminals"].pop()
    elif case == "unknown_choice":
        args["layout"]["anchor_choices"] = {"load": {"P": "missing"}}
    elif case == "multiple":
        row["terminals"][0]["anchors"].append({**pin, "id": "pin1"})
    elif case == "off_grid":
        args["layout"]["instances"]["load"]["xy"] = [4.001, 0]
    elif case == "inside_pin":
        pin["xy"] = [0, 0]
    elif case == "inward_escape":
        pin["escape"] = "down"
    elif case == "zero_box":
        row["occupied_bbox"] = [[0, 0], [0, 0]]
    with pytest.raises(CircuitSpecError):
        preview_geometry(**args)


def test_independent_geometry_problems_are_reported_together():
    """独立缺陷必须一次列全：逐条报错会让每次预览只前进一项规则。"""
    args = sample_geometry()
    geo = args["geometry"]
    args["layout"]["instances"]["load"]["xy"] = [4.001, 0]
    stim = next(row for row in geo["instances"] if row["instance"] == "stim")
    stim["terminals"][0]["anchors"][0]["escape"] = "down"
    with pytest.raises(CircuitSpecError) as failure:
        preview_geometry(**args)
    message = str(failure.value)
    assert "2 problem(s)" in message
    assert "instance origin is off grid (load)" in message
    assert "stub escape must lead out of occupied_bbox; explicit routing needed (stim/P)" in message


def test_digest_mismatch_names_the_canonical_rule_and_empty_constant():
    """错误文本必须给出序列化规则与空参数常量，现场即可重算而不必试错。"""
    args = sample_geometry()
    row = next(r for r in args["geometry"]["instances"] if r["instance"] == "dut")
    assert row["parameters_digest"] == digest({})
    row["parameters_digest"] = "changed"
    with pytest.raises(CircuitSpecError) as failure:
        preview_geometry(**args)
    message = str(failure.value)
    assert "geometry requested-parameter digest differs" in message
    assert 'sort_keys=True, separators=(",", ":")' in message
    assert "ensure_ascii=False" in message and "allow_nan=False" in message
    assert digest({}) in message


def test_preview_digest_mismatch_names_the_verbatim_resubmission_rule():
    """摘要覆盖数组顺序：错误文本必须给出"原样重发或重新预览"的规则。"""
    args = sample_geometry()
    args["preview_digest"] = "stale"
    with pytest.raises(CircuitSpecError) as failure:
        preview_geometry(**args)
    message = str(failure.value)
    assert "preview exact spec, bindings and template_use again" in message
    assert "array order" in message and "unchanged" in message


def test_reordered_bindings_arrays_change_the_preview_digest():
    """调换 bindings 端子数组顺序即改变摘要，重发必须逐字节一致。"""
    args = sample_geometry()
    master = next(m for m in args["bindings"]["masters"] if len(m["terminals"]) > 1)
    master["terminals"] = list(reversed(master["terminals"]))
    with pytest.raises(CircuitSpecError) as failure:
        preview_geometry(**args)
    assert "preview_digest differs" in str(failure.value)


def test_explicit_selection_preserves_multiple_pin_candidates():
    args = sample_geometry()
    pins = args["geometry"]["instances"][0]["terminals"][0]["anchors"]
    pins.append({"id": "pin1", "xy": [0.25, 0.5], "escape": "up"})
    args["layout"]["anchor_choices"] = {"load": {"P": "pin1"}}
    out = preview_geometry(**args)
    assert out["plan"]["geometry_checks_passed"]
    wire = next(
        w for w in out["plan"]["wires"] if w["endpoint"] == {"instance": "load", "terminal": "P"}
    )
    assert wire["anchor_id"] == "pin1" and wire["points"][0] == [4.25, 0.5]


@pytest.mark.parametrize(
    "xy,code",
    [
        ([0.75, 0], "instance_clearance"),
        ([-4, 3], "load_not_right_of_dut"),
        ([0, 1.125], "stub_hits_instance"),
    ],
)
def test_collisions_and_role_violations_are_visible_not_creation_ready(xy, code):
    args = sample_geometry()
    args["layout"]["instances"]["load"]["xy"] = xy
    out = preview_geometry(**args)
    assert out["status"] == "geometry_conflicts"
    assert code in {i["code"] for i in out["plan"]["issues"]}
    assert not out["plan"]["creation_ready"]


def _ports(args, rows):
    args["spec"]["ports"] = [{"name": n, "net": n, "direction": d} for n, d, _, _ in rows]
    args["layout"]["ports"] = {n: {"xy": xy, "escape": e} for n, _, xy, e in rows}
    args["preview_digest"] = preview_circuit(args["spec"], args["bindings"])["preview_digest"]


def test_ports_policy_and_crossing_different_net_stubs():
    args = sample_geometry()
    _ports(
        args,
        [
            ("IN", "input", [-6, -1], "left"),
            ("gnd!", "inputOutput", [-6, 1], "left"),
            ("OUT", "output", [6, 0], "right"),
        ],
    )
    assert preview_geometry(**args)["plan"]["geometry_checks_passed"]
    _ports(args, [("IN", "input", [1, 1], "up"), ("OUT", "output", [2, 2], "left")])
    args["layout"]["stub_length"] = 1
    issues = preview_geometry(**args)["plan"]["issues"]
    assert "different_net_stubs_touch" in {i["code"] for i in issues}


def test_mcp_schema_dispatch_no_live_call():
    class Client:
        def call(self, *args):
            raise AssertionError("geometry planning must not call live PDK discovery")

    args = sample_geometry()
    tool = next(t for t in GENERIC_TOOLS if t["name"] == "preview_circuit_geometry")
    validate(args, tool["inputSchema"])
    response = McpServer(Client()).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool["name"], "arguments": args},
        }
    )
    assert not response["result"]["isError"]
    assert json.loads(response["result"]["content"][0]["text"])["status"] == "geometry_checked"
    with pytest.raises(CircuitSpecError):
        call_generic(tool["name"], {**args, "code": "exit()"})


def test_box_transform_handles_asymmetric_negative_coordinates():
    assert transform_box([[-1, -2], [3, 4]], "R90", [10, 20]) == [[6, 19], [12, 23]]
