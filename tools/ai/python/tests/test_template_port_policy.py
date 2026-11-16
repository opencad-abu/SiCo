"""Template pin placement outranks default bands; routing keeps intentional stubs."""

import copy

import pytest
from test_circuit_geometry_routing import routed_sample
from test_template_placement import DEVICE_MAP, fixture, relation_view
from test_template_routing import _inputs, _record

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_port_routing import port_stub_issues
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import CircuitSpecError, digest
from cadai.template_catalog import TemplateCatalog
from cadai.template_placement_layout import plan_layout
from cadai.template_port_relations import port_relations
from cadai.template_routing import route_template


def template_ports():
    view = relation_view()
    # Deliberately unlike the fallback convention: ground above input, output
    # on the left; also exercise a mapped port name independent of its net.
    view["ports"] = [dict(name="source_in", direction="input", xy=[-160, 80], side="left"),
                     dict(name="OUT", direction="output", xy=[-160, 160], side="left"),
                     dict(name="VSS", direction="inputOutput", xy=[80, 400], side="top")]
    return view


def test_template_sides_positions_and_renamed_ports_override_default_bands():
    spec, bindings, geometry = fixture()
    layout, report = plan_layout(spec, bindings, geometry, template_ports(), DEVICE_MAP,
                                port_map={"source_in": "IN", "OUT": "OUT", "VSS": "VSS"})
    assert all(p["placement_source"] == "template" for p in layout["ports"].values())
    assert layout["ports"]["VSS"]["side"] == "top"
    assert layout["ports"]["VSS"]["escape"] == "down"
    assert layout["ports"]["OUT"]["side"] == "left"
    assert layout["ports"]["OUT"]["xy"][1] > layout["ports"]["IN"]["xy"][1]
    assert next(r for r in report["port_rows"] if r["port"] == "IN")["source_port"] == "source_in"
    plan = preview_geometry(spec, bindings, preview_circuit(spec, bindings)["preview_digest"],
                            geometry, layout)["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]
    facts = {p["name"]: p for p in plan["ports"]}
    assert facts["OUT"]["orientation"] == "R180"
    assert facts["VSS"]["orientation"] == "R270"


def test_partial_template_only_missing_pin_uses_default_without_moving_known_pin():
    spec, bindings, geometry = fixture()
    view = template_ports()
    complete, _ = plan_layout(spec, bindings, geometry, view, DEVICE_MAP,
                             port_map={"source_in": "IN", "OUT": "OUT", "VSS": "VSS"})
    view["ports"][1]["xy"] = None
    partial, report = plan_layout(spec, bindings, geometry, view, DEVICE_MAP,
                                 port_map={"source_in": "IN", "OUT": "OUT", "VSS": "VSS"})
    assert partial["ports"]["IN"] == complete["ports"]["IN"]
    assert partial["ports"]["VSS"] == complete["ports"]["VSS"]
    assert partial["ports"]["OUT"]["placement_source"] == "default"
    assert partial["ports"]["OUT"]["escape"] == "left"
    assert next(r for r in report["port_rows"] if r["port"] == "OUT")["reason"] == (
        "template_port_position_unavailable")


def test_present_pin_evidence_without_frame_is_not_silently_replaced_by_bands():
    spec, bindings, geometry = fixture()
    view = template_ports()
    view["instances"] = []
    with pytest.raises(CircuitSpecError, match="no mapped placement frame"):
        plan_layout(spec, bindings, geometry, view, {})


def test_capture_anchor_wins_over_text_bbox_and_ambiguous_pins_remain_unavailable():
    from cadai.template_schema import COORDINATE_SPACE

    record = dict(topology=dict(ports=[dict(name="A", direction="input")]), assets=dict(
        schematic=dict(coordinate_space=COORDINATE_SPACE, instances=[],
                       pins=[dict(terminal="A", figure=dict(objType="inst", xy=[20, 30],
                                                          bBox=[[-90, -90], [90, 90]],
                                                          orient="R0"))])))
    assert port_relations(record)[0]["xy"] == [20, 30]
    asset = record["assets"]["schematic"]
    asset["wire_reference"] = dict(ports=[dict(port="A", xy=[40, 50])])
    assert port_relations(record)[0]["xy"] == [40, 50]
    asset["wire_reference"]["ports"].append(dict(port="A", xy=[60, 70]))
    assert port_relations(record)[0]["xy"] is None
    assert "ambiguous" in port_relations(record)[0]["gap"]


@pytest.mark.parametrize("engine", ["planner", "cadence_route"])
def test_default_port_stubs_coexist_with_internal_routes_and_explicit_route_opt_in(engine):
    args = routed_sample(engine)
    args["layout"]["routing"].pop("ports")
    args["layout"]["ports"]["OUT"]["xy"][1] = 0.4375
    plan = preview_geometry(**args)["plan"]
    assert plan["geometry_checks_passed"], plan["issues"]
    stubs = [w for w in plan["wires"] if "port" in w["endpoint"]]
    assert len(stubs) == len(args["spec"]["ports"])
    assert all(len(w["points"]) == 2 and w.get("label") and not w.get("kind") for w in stubs)
    assert any(w.get("kind") in {"route", "cadence_route"} for w in plan["wires"])
    assert not any("port" in ref for w in plan["wires"] for ref in w.get("refs", []))
    assert plan["routing"]["port_stub_terminals"] == 3
    args["layout"]["routing"]["ports"] = "route"
    assert any(i["code"] == "port_off_terminal_row"
               for i in preview_geometry(**args)["plan"]["issues"])


def test_template_route_prunes_port_leaf_and_preserves_device_path(monkeypatch):
    record = _record()
    top = record["topology"]
    top["ports"] = [dict(name="P", net="n0", direction="input")]
    asset = record["assets"]["schematic"]
    asset["wire_reference"]["ports"] = [dict(port="P", id="a0", xy=[-4, 0])]
    asset["shapes"].append(dict(net="N", figure=dict(lpp=["wire", "drawing"], objType="line",
                                                    points=[[-4, 0], [0, 0]])))
    plan, geometry, layout, members, placed, pins = _inputs(record)
    plan["spec"] = dict(ports=[dict(name="N", net="N", direction="input")])
    plan["template_use"]["port_map"] = {"P": "N"}
    layout["ports"] = {"N": dict(xy=[6, 5], escape="right")}
    stub = dict(endpoint=dict(port="N"), net="N", points=[[6, 5], [7, 5]],
                label=dict(text="N", xy=[7, 5]))
    members.append(stub)
    pins.append(dict(endpoint=dict(port="N"), net="N", xy=[6, 5]))
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    wires, report = route_template(plan, geometry, layout, members, placed, pins)
    assert report["nets"][0]["status"] == "reused", report
    assert report["port_stub_terminals"] == 1
    assert stub in wires
    assert len(wires) == 2
    assert next(w for w in wires if w is not stub)["points"] == [[10, 5], [14, 5]]
    # Corrupt source evidence must not become valid because a port was removed.
    broken = copy.deepcopy(record)
    broken["assets"]["schematic"]["wire_reference"]["gaps"] = [
        dict(code="source_wire_coordinate_rounded")]
    plan["template_evidence"]["wire_reference_digest"] = digest(
        broken["assets"]["schematic"]["wire_reference"])
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: broken)
    _, report = route_template(plan, geometry, layout, members, placed, pins)
    assert report["nets"][0]["status"] == "stub"


def test_same_net_stubs_must_not_merge_or_duplicate_the_label_at_the_port():
    plan = dict(spec=dict(ports=[dict(name="A", net="A", direction="input")]))
    layout = dict(ports=dict(A=dict(xy=[-2, 0], escape="right")), clearance=0.125)
    wires = [dict(endpoint=dict(port="A"), net="A", points=[[-2, 0], [-1, 0]]),
             dict(endpoint=dict(instance="M", terminal="G"), net="A",
                  points=[[0, 0], [-1, 0]])]
    assert port_stub_issues(plan, layout, [[0, -1], [1, 1]], wires) == [
        dict(code="wire_touches_port_stub_region", port="A",
             endpoint=dict(instance="M", terminal="G"))]
    wires[1]["points"][-1] = [-0.5, 0]
    assert not port_stub_issues(plan, layout, [[0, -1], [1, 1]], wires)


def test_known_template_positions_do_not_disable_real_collision_checks():
    args = routed_sample()
    args["layout"]["ports"]["OUT"].update(xy=[0, 0], placement_source="template")
    assert any(i["code"] == "port_on_instance"
               for i in preview_geometry(**args)["plan"]["issues"])
