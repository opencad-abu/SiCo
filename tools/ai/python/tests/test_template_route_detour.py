"""Bounded edge repair succeeds only when the original graph survives unchanged."""

import copy

import pytest
from test_template_route_deform import bus_case, deformed
from test_template_shared_terminals import shared_bus

from cadai import template_route_deform as deformation
from cadai import template_route_detour as detour
from cadai.circuit_geometry_schema import MATRICES, on_grid, transform
from cadai.template_route_deform import deform_paths
from cadai.template_route_paths import path_gaps
from cadai.template_wire_attachment import compile_reference


@pytest.fixture(autouse=True)
def fixed_vertex_solver_only(monkeypatch):
    # These unit tests isolate detour refusal; the free-track fallback has its
    # own integration tests and may legitimately solve the same geometry.
    monkeypatch.setattr(deformation, "repair_paths", detour.detour_paths)


def attempt(record, inputs, reserved, diagnostics):
    plan, geometry, layout, members, placed, pins = inputs
    source = compile_reference(record)
    return deform_paths(source["nets"]["n0"], source, plan["template_use"], members,
                        geometry, layout, placed, pins, reserved, diagnostics)


def foreign(orientation="R0"):
    return [dict(net="other", points=[transform(p, orientation, [10, 20])
                                      for p in ([5, 1], [5, 3])])]


@pytest.mark.parametrize("orientation", list(MATRICES))
def test_detour_keeps_original_vertices_and_endpoints_in_all_orientations(orientation):
    record, inputs = bus_case(orientation=orientation)
    before = deformed(record, inputs)
    reserved = foreign(orientation)
    diagnostics = []
    paths = attempt(record, inputs, reserved, diagnostics)
    assert paths and len(paths) == len(before) + 2
    assert {tuple(p) for w in before for p in w["points"]} < {
        tuple(p) for w in paths for p in w["points"]}
    assert not path_gaps(paths, inputs[3], inputs[2], inputs[4], inputs[5], reserved)
    assert [d["code"] for d in diagnostics] == ["target_local_detour_applied"]
    assert sum(w["label"] is not None for w in paths) == 1


@pytest.mark.parametrize("setting,value", [("max_detour", 1), ("max_bends", 1), ("max_bends", 2)])
def test_requested_limits_preserve_safe_failure(setting, value):
    record, inputs = bus_case()
    inputs[2]["routing"][setting] = value
    diagnostics = []
    assert attempt(record, inputs, foreign(), diagnostics) is None
    assert diagnostics[0]["code"] == "target_local_detour_exhausted"
    assert diagnostics[-1]["code"] == "target_path_constraints"


@pytest.mark.parametrize("field", ["MAX_CANDIDATES", "MAX_OBSTACLES", "MAX_SEGMENTS"])
def test_budget_exhaustion_is_reported_without_unbounded_search(monkeypatch, field):
    record, inputs = bus_case()
    monkeypatch.setattr(detour, field, 0)
    diagnostics = []
    assert attempt(record, inputs, foreign(), diagnostics) is None
    row = diagnostics[0]
    assert row["code"] == "target_local_detour_exhausted" and row["candidates"] == 0
    assert row["reasons"][0] in {"local_detour_candidate_limit", "local_detour_work_limit"}


def test_multiple_colliding_edges_are_repaired_atomically():
    record, inputs = bus_case()
    diagnostics = []
    reserved = foreign() + [dict(net="other", points=[[32, 21], [32, 23]])]
    paths = attempt(record, inputs, reserved, diagnostics)
    assert paths
    assert len(diagnostics[0]["source_edges"]) == 2
    assert not path_gaps(paths, inputs[3], inputs[2], inputs[4], inputs[5], reserved)


@pytest.mark.parametrize("path", [
    [[10, 22], [10, 19], [17, 19], [17, 22]],  # existing vertices and overlap
    [[10, 22], [10, 20], [17, 20], [17, 22]],  # new T contacts / overlap
    [[10, 22], [10, 24], [26, 24], [26, 22], [17, 22]],  # merge another junction
])
def test_contact_or_overlap_cannot_become_a_success(monkeypatch, path):
    record, inputs = bus_case()
    inputs[2]["routing"]["max_detour"] = 20
    monkeypatch.setattr(detour, "orthogonal_candidates", lambda *args: [path])
    diagnostics = []
    assert attempt(record, inputs, foreign(), diagnostics) is None
    assert "target_graph_not_preserved" in diagnostics[0]["candidate_rejections"]


@pytest.mark.parametrize("obstacle", ["body", "pin", "wire"])
def test_detour_rechecks_all_target_obstacles(monkeypatch, obstacle):
    record, inputs = bus_case()
    reserved = foreign()
    if obstacle == "body":
        inputs[4]["new_body"] = dict(bbox=[[12, 23], [14, 25]])
        expected = "template_path_hits_instance"
    elif obstacle == "pin":
        inputs[5].append(dict(net="third", xy=[13, 24]))
        expected = "template_path_hits_other_net_pin"
    else:
        reserved.append(dict(net="third", points=[[13, 23.5], [13, 24.5]]))
        expected = "template_paths_different_nets_touch"
    monkeypatch.setattr(detour, "orthogonal_candidates", lambda *args: [
        [[10, 22], [10, 24], [17, 24], [17, 22]]])
    diagnostics = []
    assert attempt(record, inputs, reserved, diagnostics) is None
    assert expected in diagnostics[0]["candidate_rejections"]


def test_non_grid_obstacle_bounds_produce_bounded_outward_grid_guides():
    guides = detour._guides([[[1.1, 2.3], [3.3, 4.7]]], [0, 0], [5, 0], .5, .1)
    assert guides == dict(x=[4, .5], y=[1.5, 5.5])
    assert all(on_grid(values, .5) for values in guides.values())


def test_input_order_and_names_do_not_change_detoured_geometry():
    record, inputs = bus_case()
    saved = copy.deepcopy(inputs)
    before = attempt(record, inputs, foreign(), [])
    assert inputs == saved
    inputs[0]["template_use"]["devices"].reverse()
    inputs[3].reverse()
    inputs[5].reverse()
    for row in inputs[3] + inputs[5]:
        row["net"] = "renamed"
    after = attempt(record, inputs, foreign(), [])
    assert [w["points"] for w in before] == [w["points"] for w in after]


def test_source_gap_cannot_become_a_detour():
    record, inputs = bus_case()
    record["assets"]["schematic"]["wire_reference"]["gaps"] = [dict(code="unproved")]
    diagnostics = []
    assert attempt(record, inputs, foreign(), diagnostics) is None
    assert [d["code"] for d in diagnostics] == ["source_attachment_gaps"]


@pytest.mark.parametrize("orientation", list(MATRICES))
def test_shared_terminal_identities_survive_a_local_detour(orientation):
    record, inputs = shared_bus(orientation)
    source = compile_reference(record)["nets"]["n0"]
    assert len(source["anchors"]) == 8 and len(source["terminal_contacts"]) == 4
    diagnostics = []
    paths = attempt(record, inputs, foreign(orientation), diagnostics)
    assert paths and diagnostics[0]["code"] == "target_local_detour_applied"
    assert len(inputs[3]) == 8
    assert not path_gaps(paths, inputs[3], inputs[2], inputs[4], inputs[5], foreign(orientation))


@pytest.mark.parametrize("orientation", list(MATRICES))
def test_multiple_repairs_keep_shared_contacts_and_identity(orientation):
    record, inputs = shared_bus(orientation)
    reserved = foreign(orientation) + [dict(net="other", points=[
        transform(p, orientation, [10, 20]) for p in ([22, 1], [22, 3])])]
    paths = attempt(record, inputs, reserved, [])
    assert paths and len(inputs[3]) == 8
    assert not path_gaps(paths, inputs[3], inputs[2], inputs[4], inputs[5], reserved)


@pytest.mark.parametrize("budget", ["MAX_REPAIR_EDGES", "MAX_SEGMENTS"])
def test_multiple_edge_work_limit_is_checked_before_search(monkeypatch, budget):
    record, inputs = bus_case()
    reserved = foreign() + [dict(net="other", points=[[32, 21], [32, 23]])]
    diagnostics = []
    count = len(deformed(record, inputs))
    monkeypatch.setattr(detour, budget, 1 if budget == "MAX_REPAIR_EDGES" else count + 2)
    assert attempt(record, inputs, reserved, diagnostics) is None
    assert diagnostics[0]["candidates"] == 0
    assert diagnostics[0]["reasons"][0] in {"local_detour_edge_limit", "local_detour_work_limit"}
