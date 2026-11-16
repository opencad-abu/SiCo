"""Unrelated terminal levels may exchange order only with full graph evidence."""

import copy

import pytest
from test_template_route_deform import bus_case
from test_template_shared_terminals import attest

from cadai import template_route_axis as axis
from cadai.circuit_geometry_schema import MATRICES, transform, transform_box
from cadai.circuit_spec_schema import digest
from cadai.template_catalog import TemplateCatalog
from cadai.template_route_deform import deform_paths
from cadai.template_route_paths import path_gaps
from cadai.template_routing import route_template
from cadai.template_wire_attachment import compile_reference


def case(orientation="R0", shared=False):
    record, inputs = bus_case(3, orientation)
    plan, geometry, layout, members, placed, pins = inputs
    asset = record["assets"]["schematic"]
    source_y, target_y = [4, 6, 8], [5, 3, 7]
    lines = [[[0, 0], [8, 0]]]
    box = [[-.5, 0], [.5, 2]]
    for i in range(3):
        asset["instances"][i]["relative_xy"] = [4*i, source_y[i]]
        geo = asset["wire_reference"]["geometry"][i]
        geo["occupied_bbox"] = box
        if shared:
            geo["terminals"][1]["anchors"][0]["xy"] = [0, 0]
        lines.append([[4*i, 0], [4*i, source_y[i]+(0 if shared else 1)]])
        key = f"target{i}"
        xy = transform([7*i, target_y[i]], orientation, [10, 20])
        layout["instances"][key]["xy"] = xy
        geometry[key]["occupied_bbox"] = box
        placed[key]["bbox"] = transform_box(box, orientation, xy)
        for j in (0, 1):
            offset = 0 if shared else j
            geometry[key]["terminals"][j]["anchors"][0]["xy"] = [0, offset]
            members[2*i+j]["points"] = [transform([0, offset], orientation, xy),
                                        transform([0, offset-.5], orientation, xy)]
            pins[2*i+j]["xy"] = members[2*i+j]["points"][0]
    asset["shapes"] = [dict(net="N", figure=dict(lpp=["wire", "drawing"], objType="line",
                                                points=p)) for p in lines]
    if shared:
        attest(record)
    plan["template_evidence"]["wire_reference_digest"] = digest(asset["wire_reference"])
    return record, inputs


def attempt(record, inputs, diagnostics, reserved=(), pending=None):
    source = compile_reference(record)
    plan, geometry, layout, members, placed, pins = inputs
    return deform_paths(source["nets"]["n0"], source, plan["template_use"], members,
                        geometry, layout, placed, pins, reserved, diagnostics, pending=pending)


@pytest.mark.parametrize("orientation", list(MATRICES))
@pytest.mark.parametrize("shared", [False, True])
def test_unrelated_levels_reverse_without_reversing_any_edge(orientation, shared):
    record, inputs = case(orientation, shared)
    before = copy.deepcopy(inputs)
    diagnostics = []
    paths = attempt(record, inputs, diagnostics)
    assert paths, diagnostics
    assert inputs == before
    assert not path_gaps(paths, inputs[3], inputs[2], inputs[4], inputs[5])
    assert [d["code"] for d in diagnostics] == ["axis_track_order_reversed",
                                               "target_local_order_graph_verified"]
    assert len(paths) == len(compile_reference(record)["nets"]["n0"]["edges"])
    assert {tuple(m["points"][0]) for m in inputs[3]} <= {
        tuple(p) for w in paths for p in w["points"]}


def test_public_routing_reports_actual_local_order_method(monkeypatch):
    record, inputs = case()
    monkeypatch.setattr(TemplateCatalog, "get", lambda *a: record)
    _, report = route_template(*inputs)
    assert report["nets"][0]["method"] == "local_order_deformation"
    assert report["metrics"]["routed_terminals"] == 6
    assert "transform" not in report["nets"][0]


@pytest.mark.parametrize("target", [-1, 0])
def test_reversed_or_collapsed_real_edge_still_refuses(target):
    edges = [dict(shape=0, segment=0, points=[[0, 0], [0, 2]])]
    diagnostics = []
    assert axis.local_order_maps(edges, [([0, 0], [0, 0]), ([0, 2], [0, target])],
                                 .5, 1, diagnostics) is None
    assert diagnostics[-1]["code"] == "local_order_edge_reversed_or_collapsed"


@pytest.mark.parametrize("budget", ["MAX_LOCAL_EDGES", "MAX_LOCAL_ANCHORS"])
def test_local_map_has_bounded_work(monkeypatch, budget):
    record, inputs = case()
    monkeypatch.setattr(axis, budget, 0)
    diagnostics = []
    assert attempt(record, inputs, diagnostics) is None
    assert diagnostics[-1]["code"] == "local_order_work_limit"


@pytest.mark.parametrize("fault", ["source", "anchor", "off_grid", "shared_split", "body", "pin"])
def test_nonmonotone_candidate_cannot_bypass_existing_guards(fault):
    record, inputs = case(shared=True)
    pending = []
    if fault == "source":
        record["assets"]["schematic"]["wire_reference"]["gaps"] = [dict(code="unproved")]
    elif fault == "anchor":
        inputs[1]["target0"]["terminals"] = []
    elif fault == "off_grid":
        inputs[3][0]["points"][0][0] += .1
    elif fault == "shared_split":
        inputs[3][1]["points"][0] = [99, 99]
    elif fault == "body":
        inputs[4]["blocker"] = dict(bbox=[[12, 20], [14, 22]])
    else:
        inputs[5].append(dict(net="other", xy=[13, 21]))
    assert attempt(record, inputs, [], pending=pending) is None
    assert pending == []


def test_complete_graph_rejects_new_crossing_even_if_each_edge_keeps_direction():
    from cadai.template_route_paths import graph_preserved

    edges = [dict(shape=i, segment=0, points=ps) for i, ps in enumerate([
        [[0, 2], [4, 2]], [[4, 2], [4, 6]], [[4, 6], [2, 6]],
        [[2, 6], [2, 5]], [[2, 5], [2, 3]]])]
    anchors = [([0, 2], [0, 3]), ([2, 3], [2, 2]), ([2, 5], [2, 5])]
    old = [dict(xy=a, endpoint=dict(instance=f"d{i}", terminal="p"))
           for i, (a, _) in enumerate(anchors)]
    assert graph_preserved(edges, old, 6, .5)
    maps = axis.local_order_maps(edges, anchors, .5, 1, [])
    assert maps  # All edge directions are individually valid.
    moved = [dict(e, points=[[maps[k][p[k]] for k in (0, 1)] for p in e["points"]])
             for e in edges]
    new = [dict(a, xy=b) for a, (_, b) in zip(old, anchors)]
    assert not graph_preserved(moved, new, 6, .5)


def test_determinism_under_names_and_input_order():
    record, inputs = case(shared=True)
    before = attempt(record, inputs, [])
    inputs[0]["template_use"]["devices"].reverse()
    inputs[3].reverse()
    inputs[5].reverse()
    for row in inputs[3] + inputs[5]:
        row["net"] = "renamed"
    after = attempt(record, inputs, [])
    assert [w["points"] for w in before] == [w["points"] for w in after]


def test_verified_local_order_graph_may_wait_for_wire_coordination(monkeypatch):
    from cadai import template_route_deform as deform

    record, inputs = case(shared=True)
    monkeypatch.setattr(deform, "repair_paths", lambda *a: None)
    pending, diagnostics = [], []
    foreign = [dict(net="other", points=[[13, 20.5], [13, 21.5]])]
    assert attempt(record, inputs, diagnostics, foreign, pending) is None
    assert len(pending) == 1
    assert not path_gaps(pending[0], inputs[3], inputs[2], inputs[4], inputs[5])
    assert diagnostics[-1] == dict(code="target_path_constraints",
                                   reasons=["template_paths_different_nets_touch"])
    assert any(d["code"] == "target_local_order_graph_verified" for d in diagnostics)


def test_track_conflict_is_not_retried_as_local_order(monkeypatch):
    from cadai import template_route_deform as deform

    record, inputs = case(shared=True)
    # Separate two target contacts that occupied the same source y track.
    # This is also caught by shared identity before axis mapping.
    inputs[3][1]["points"][0] = [10, 25.5]
    called = []
    monkeypatch.setattr(deform, "local_order_maps", lambda *a: called.append(a))
    assert attempt(record, inputs, []) is None
    assert not called


def test_axis_constraint_conflicts_and_off_grid_still_refuse_local_map():
    edges = [dict(shape=0, segment=0, points=[[0, 0], [0, 2]])]
    for anchors, code in [
        ([([0, 0], [0, 0]), ([0, 2], [.5, 2])], "axis_track_conflict"),
        ([([0, 0], [0, 0]), ([0, 2], [0, 2.1])], "target_anchor_off_grid"),
    ]:
        diagnostics = []
        assert axis.local_order_maps(edges, anchors, .5, 1, diagnostics) is None
        assert any(d["code"] == code for d in diagnostics)
