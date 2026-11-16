"""PDK-neutral multi-terminal graphs with simultaneous device/terminal movement."""

import copy

import pytest
from test_template_routing import _record

from cadai.circuit_geometry_schema import MATRICES, transform, transform_box
from cadai.circuit_spec_schema import digest
from cadai.template_catalog import TemplateCatalog
from cadai.template_route_deform import _axis_map, deform_paths
from cadai.template_route_paths import path_gaps, repair_branches
from cadai.template_routing import route_template
from cadai.template_wire_attachment import compile_reference


def bus_case(count=4, orientation="R0"):
    record = _record()
    record["source"]["cell"] = "branched_network"
    master = dict(library="testDevices", cell="two_terminal", view="symbol")
    record["topology"]["devices"] = [dict(
        id=f"d{i}", master=master, pins=[dict(name=t, net="n0") for t in ("lo", "hi")])
        for i in range(count)]
    asset = record["assets"]["schematic"]
    asset["instances"] = [dict(device=f"d{i}", relative_xy=[4*i, 0], orient="R0")
                          for i in range(count)]
    box = [[0, -1], [1, 1]]
    asset["wire_reference"]["geometry"] = [dict(
        device=f"d{i}", library_path="/test/devices", revision="source",
        occupied_bbox=box, terminals=[dict(name=t, anchors=[dict(id="a", xy=[0, j])])
                                     for j, t in enumerate(("lo", "hi"))]) for i in range(count)]
    lines = [[[0, 4], [4*(count-1), 4]]]
    lines += [[[4*i, 0], [4*i, 4]] for i in range(count)]
    asset["shapes"] = [dict(net="N", figure=dict(lpp=["wire", "drawing"], objType="line",
                                                points=points)) for points in lines]
    use = dict(template_ref=record["template_ref"], net_map=dict(n0="rail"), port_map={},
               devices=[dict(source_device=f"d{i}", instance=f"target{i}", master="m",
                             terminal_map=dict(lo="lower", hi="upper")) for i in range(count)])
    plan = dict(template_use=use, template_evidence=dict(
        wire_reference_digest=digest(asset["wire_reference"])), bindings=dict(masters=[dict(
            id="m", target=master, library_path="/test/devices")]))
    layout = dict(grid=.5, clearance=0, anchor_choices={},
                  routing=dict(mode="end_to_end", engine="template", fallback="reject"),
                  instances={})
    geometry, placed, members, pins = {}, {}, [], []
    for i in range(count):
        key = f"target{i}"
        xy = transform([6*i+i*i, -2], orientation, [10, 20])
        layout["instances"][key] = dict(xy=xy, orientation=orientation)
        geometry[key] = dict(occupied_bbox=box, terminals=[dict(
            name=t, anchors=[dict(id="selected", xy=[0, j])])
            for j, t in enumerate(("lower", "upper"))])
        placed[key] = dict(bbox=transform_box(box, orientation, xy))
        for j, term in enumerate(("lower", "upper")):
            endpoint = dict(instance=key, terminal=term)
            point = transform([0, j], orientation, xy)
            members.append(dict(endpoint=endpoint, net="rail", points=[
                point, transform([0, j + .5], orientation, xy)]))
            pins.append(dict(endpoint=endpoint, net="rail", xy=point))
    return record, (plan, geometry, layout, members, placed, pins)


def deformed(record, inputs, reserved=()):
    plan, geometry, layout, members, placed, pins = inputs
    source = compile_reference(record)
    return deform_paths(source["nets"]["n0"], source, plan["template_use"], members,
                        geometry, layout, placed, pins, reserved)


@pytest.mark.parametrize("orientation", list(MATRICES))
@pytest.mark.parametrize("count", [2, 4, 6])
def test_simultaneously_moved_terminal_contacts_and_junctions_keep_graph(orientation, count):
    record, inputs = bus_case(count, orientation)
    plan, _, layout, members, placed, pins = inputs
    source = compile_reference(record)
    assert source["nets"]["n0"]["gaps"] == []
    # Degree-two terminal contacts cannot be handled by the leaf-only repair.
    assert repair_branches(source["nets"]["n0"], source, plan["template_use"], members,
                           layout, placed, pins, []) is None
    paths = deformed(record, inputs)
    assert paths
    assert not path_gaps(paths, members, layout, placed, pins)
    assert len(paths) == len(source["nets"]["n0"]["edges"])
    assert {tuple(p) for m in members for p in m["points"][:1]} <= {
        tuple(p) for w in paths for p in w["points"]}
    assert sum(w["label"] is not None for w in paths) == 1


def test_routing_pipeline_reports_deformation_without_a_fictitious_rigid_transform(monkeypatch):
    record, inputs = bus_case()
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    wires, report = route_template(*inputs)
    row = report["nets"][0]
    assert row["status"] == "rerouted" and row["method"] == "orthogonal_deformation"
    assert "transform" not in row
    assert report["metrics"]["routed_terminals"] == 8
    assert not report["issues"] and len(wires) > 1


@pytest.mark.parametrize("fault", ["obstacle", "foreign_pin", "same_net_port",
                                  "missing_geometry", "ambiguous_anchor", "off_grid", "source_gap"])
def test_deformation_refuses_unsafe_or_unproved_paths(fault):
    record, inputs = bus_case()
    _, geometry, _, members, placed, pins = inputs
    reserved = []
    if fault in {"obstacle", "same_net_port"}:
        # The same-net independent port region is an obstacle too.
        placed[fault] = dict(bbox=[[14, 21], [16, 23]])
    elif fault == "foreign_pin":
        pins.append(dict(xy=[15, 22], net="other"))
    elif fault == "missing_geometry":
        geometry["target0"]["terminals"] = []
    elif fault == "ambiguous_anchor":
        geometry["target0"]["terminals"][0]["anchors"].append(dict(id="second", xy=[0, .5]))
    elif fault == "off_grid":
        members[0]["points"][0][0] += .1
    else:
        record["assets"]["schematic"]["wire_reference"]["gaps"] = [dict(code="unproved")]
    assert deformed(record, inputs, reserved) is None


@pytest.mark.parametrize("x", [10, 5])
def test_collapsed_or_reversed_tracks_are_rejected(x):
    record, inputs = bus_case()
    _, _, layout, members, placed, pins = inputs
    dx = x - layout["instances"]["target1"]["xy"][0]
    layout["instances"]["target1"]["xy"][0] += dx
    for row in members:
        if row["endpoint"]["instance"] == "target1":
            for point in row["points"]:
                point[0] += dx
    # Pins share their selected member anchor list.
    placed["target1"]["bbox"] = [[v[0]+dx, v[1]] for v in placed["target1"]["bbox"]]
    assert deformed(record, inputs) is None


def test_order_and_net_names_do_not_change_deformed_geometry():
    record, inputs = bus_case()
    before = deformed(record, inputs)
    changed = copy.deepcopy(inputs)
    changed[0]["template_use"]["devices"].reverse()
    changed[3].reverse()
    for member in changed[3]:
        member["net"] = "renamed"
    for pin in changed[5]:
        pin["net"] = "renamed"
    after = deformed(record, changed)
    assert [w["points"] for w in before] == [w["points"] for w in after]
    assert all(w["net"] == "renamed" for w in after)


def test_target_terminal_offsets_can_change_without_moving_an_instance():
    record, inputs = bus_case()
    _, geometry, _, members, placed, _ = inputs
    for key, row in geometry.items():
        row["terminals"][1]["anchors"][0]["xy"][1] = 2
        row["occupied_bbox"][1][1] = 2
        placed[key]["bbox"][1][1] += 1
    for member in members:
        if member["endpoint"]["terminal"] == "upper":
            for point in member["points"]:
                point[1] += 1
    assert deformed(record, inputs)


@pytest.mark.parametrize("shape", ["tree", "loop"])
def test_non_bus_graphs_preserve_branch_adjacency(shape):
    record, inputs = bus_case(4)
    _, _, layout, members, placed, _ = inputs
    asset = record["assets"]["schematic"]
    lines = [[[0, 0], [0, 4]], [[4, 0], [4, 4]], [[8, 0], [8, 4]],
             [[12, 0], [12, 4]], [[0, 4], [4, 4]], [[8, 4], [12, 4]],
             [[4, 4], [4, 6]], [[4, 6], [8, 6]], [[8, 4], [8, 6]]]
    if shape == "loop":
        lines.append([[4, 4], [8, 4]])
    asset["shapes"] = [dict(net="N", figure=dict(lpp=["wire", "drawing"], objType="line",
                                                points=p)) for p in lines]
    # All devices move vertically too; varying target dimensions constrain y.
    for key, position in layout["instances"].items():
        position["xy"][1] += 3
        placed[key]["bbox"] = [[v[0], v[1]+3] for v in placed[key]["bbox"]]
    for member in members:
        for point in member["points"]:
            point[1] += 3
    source = compile_reference(record)
    assert not source["nets"]["n0"]["gaps"]
    paths = deformed(record, inputs)
    assert paths
    assert len(paths) == len(source["nets"]["n0"]["edges"])
    assert {(w["template_source"]["shape"], w["template_source"]["segment"]) for w in paths} == {
        (e["shape"], e["segment"]) for e in source["nets"]["n0"]["edges"]}


def test_conflicting_terminal_tracks_do_not_guess_a_layout_change():
    record, inputs = bus_case()
    _, _, layout, members, placed, _ = inputs
    layout["instances"]["target1"]["xy"][1] += .5
    placed["target1"]["bbox"] = [[v[0], v[1]+.5] for v in placed["target1"]["bbox"]]
    for member in members:
        if member["endpoint"]["instance"] == "target1":
            for point in member["points"]:
                point[1] += .5
    assert deformed(record, inputs) is None


def test_deformation_reports_reversed_axis_tracks():
    diagnostics = []
    assert _axis_map({0, 10}, [(0, 5), (10, 4)], 1, 1, diagnostics, "y") is None
    assert any(row["code"] == "axis_track_order_reversed" for row in diagnostics)


def test_deformation_reports_target_collision_reason():
    record, inputs = bus_case()
    plan, geometry, layout, members, placed, pins = inputs
    diagnostics = []
    placed["blocker"] = {"bbox": [[8, 18], [12, 24]]}
    source = compile_reference(record)["nets"]["n0"]
    assert deform_paths(source, compile_reference(record), plan["template_use"], members,
                        geometry, layout, placed, pins, [], diagnostics) is None
    assert any(row["code"] == "target_path_constraints" and
               "template_path_hits_instance" in row["reasons"] for row in diagnostics)


def test_source_gap_cannot_be_cleared_by_pipeline_deformation(monkeypatch):
    record, inputs = bus_case()
    capture = record["assets"]["schematic"]["wire_reference"]
    capture["gaps"] = [dict(code="different_source_nets_touch")]
    inputs[0]["template_evidence"]["wire_reference_digest"] = digest(capture)
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    wires, report = route_template(*inputs)
    assert wires == inputs[3]
    assert report["nets"][0]["status"] == "rejected"
    assert report["nets"][0]["reasons"] == ["different_source_nets_touch"]
    assert report["issues"]


def test_explicit_selected_anchor_is_used_and_unselected_geometry_does_not_move_wire():
    record, inputs = bus_case()
    _, geometry, layout, _, _, _ = inputs
    geometry["target0"]["terminals"][0]["anchors"].append(dict(id="other", xy=[.5, 0]))
    layout["anchor_choices"] = dict(target0=dict(lower="selected"))
    assert deformed(record, inputs)
    layout["anchor_choices"]["target0"]["lower"] = "other"
    assert deformed(record, inputs) is None


def test_same_source_track_with_conflicting_actual_target_pins_is_rejected():
    record, inputs = bus_case()
    _, geometry, _, members, _, _ = inputs
    geometry["target0"]["terminals"][0]["anchors"][0]["xy"][0] = .5
    for point in members[0]["points"]:
        point[0] += .5
    # Both source terminals share x, but target anchors no longer do.
    assert deformed(record, inputs) is None


def test_new_contacts_after_free_track_snapping_are_rejected():
    from cadai.template_route_deform import _graph_preserved

    # Different source branches may acquire a T contact after coordinate snapping;
    # endpoint coverage alone would accept the resulting graph, adjacency must not.
    edges = [dict(shape=i, segment=0, points=p) for i, p in enumerate([
        [[0, 0], [4, 0]], [[2, 0], [2, 2]], [[2, 2], [4, 2]], [[4, 2], [4, 0]]])]
    assert not _graph_preserved(edges, [{"xy": [0, 0]}, {"xy": [4, 0]}], 5, 1)
