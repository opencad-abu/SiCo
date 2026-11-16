"""Free tracks retain graph identity, terminal coordinates and all target guards."""

import copy

import pytest
from test_template_route_deform import bus_case, deformed
from test_template_route_detour import attempt
from test_template_shared_terminals import shared_bus

from cadai import template_route_tracks as tracks
from cadai.circuit_geometry_schema import MATRICES, transform
from cadai.template_catalog import TemplateCatalog
from cadai.template_route_detour import detour_paths
from cadai.template_route_finalize import finalize_routes
from cadai.template_route_paths import path_gaps
from cadai.template_routing import route_template


def case(orientation="R0", shared=False):
    record, inputs = shared_bus(orientation) if shared else bus_case(orientation=orientation)
    before = deformed(record, inputs)
    # Block an actual junction: no fixed-vertex edge replacement can clear it.
    reserved = [dict(net="foreign", points=[transform(p, orientation, [10, 20])
                                            for p in ([6.5, 2], [7.5, 2])])]
    edges = [dict(w["template_source"], points=w["points"]) for w in before]
    return record, inputs, edges, reserved


def run(inputs, edges, reserved, diagnostics):
    _, _, layout, members, placed, pins = inputs
    return tracks.track_paths(edges, members, layout, placed, pins, reserved, diagnostics)


@pytest.mark.parametrize("orientation", list(MATRICES))
@pytest.mark.parametrize("shared", [False, True])
def test_junction_collision_moves_whole_free_bus_with_terminal_identities(orientation, shared):
    record, inputs, edges, reserved = case(orientation, shared)
    old = copy.deepcopy((inputs, edges, reserved))
    diagnostics = []
    assert detour_paths(edges, inputs[3], inputs[2], inputs[4], inputs[5], reserved, []) is None
    paths = run(inputs, edges, reserved, diagnostics)
    assert paths and len(paths) == len(edges)
    assert diagnostics[-1]["code"] == "target_free_track_applied"
    assert diagnostics[-1]["vertices"] == 4
    assert not path_gaps(paths, inputs[3], inputs[2], inputs[4], inputs[5], reserved)
    assert (inputs, edges, reserved) == old
    assert {tuple(m["points"][0]) for m in inputs[3]} <= {
        tuple(p) for w in paths for p in w["points"]}
    assert [w["template_source"] for w in paths] == [
        {k: e[k] for k in ("shape", "segment")} for e in edges]
    assert attempt(record, inputs, reserved, []) == paths


def test_connected_components_do_not_move_unrelated_equal_coordinate_tracks():
    edges = [dict(points=p) for p in ([[0, 2], [1, 2]], [[4, 2], [5, 2]],
                                     [[4, 0], [4, 2]])]
    found = list(tracks._free_tracks(edges, {(0, 2), (4, 0)}))
    assert found == [(1, {(4, 2), (5, 2)})]


@pytest.mark.parametrize("budget", ["MAX_CANDIDATES", "MAX_TRACKS", "MAX_SEGMENTS",
                                    "MAX_OBSTACLES"])
def test_work_budgets_return_no_partial_graph(monkeypatch, budget):
    _, inputs, edges, reserved = case()
    monkeypatch.setattr(tracks, budget, 0)
    diagnostics = []
    assert run(inputs, edges, reserved, diagnostics) is None
    assert diagnostics[-1]["candidates"] == 0
    assert diagnostics[-1]["code"] == "target_free_track_exhausted"


@pytest.mark.parametrize("obstacle", ["body", "pin", "wire", "same_net_port"])
def test_moved_candidate_rechecks_all_obstacles(monkeypatch, obstacle):
    _, inputs, edges, reserved = case()
    # Force the otherwise legal rail at y=21.5 through a new obstacle.
    monkeypatch.setattr(tracks, "_guides", lambda *args: dict(x=[], y=[21.5]))
    if obstacle in {"body", "same_net_port"}:
        inputs[4][obstacle] = dict(bbox=[[13, 21.4], [14, 21.6]])
        reason = "template_path_hits_instance"
    elif obstacle == "pin":
        inputs[5].append(dict(net="third", xy=[13, 21.5]))
        reason = "template_path_hits_other_net_pin"
    else:
        reserved.append(dict(net="third", points=[[13, 21.4], [13, 21.6]]))
        reason = "template_paths_different_nets_touch"
    diagnostics = []
    assert run(inputs, edges, reserved, diagnostics) is None
    assert reason in diagnostics[-1]["candidate_rejections"]


def test_terminal_track_cannot_move_or_change_contact_identity():
    _, inputs, edges, reserved = case()
    anchor = dict(endpoint=dict(instance="middle", terminal="pin"), net="rail",
                  points=[[17, 22], [17, 22.5]])
    inputs[3].append(anchor)
    diagnostics = []
    assert run(inputs, edges, reserved, diagnostics) is None
    assert diagnostics[-1]["candidates"] == 0


@pytest.mark.parametrize("value", [19, 25])
def test_branch_direction_and_length_limits_are_enforced(monkeypatch, value):
    _, inputs, edges, reserved = case()
    monkeypatch.setattr(tracks, "_guides", lambda *args: dict(x=[], y=[value]))
    inputs[2]["routing"]["max_detour"] = 1
    diagnostics = []
    assert run(inputs, edges, reserved, diagnostics) is None
    assert "free_track_direction_or_length_limit" in diagnostics[-1]["candidate_rejections"]


def test_new_same_net_contact_and_vertex_merge_are_rejected(monkeypatch):
    # A loop's top track would touch a third terminal inside the loop. Edge
    # directions and lengths remain valid, so graph recompilation must catch it.
    edges = [dict(shape=i, segment=0, points=p) for i, p in enumerate([
        [[0, 0], [0, 4]], [[0, 4], [4, 4]], [[4, 4], [4, 0]],
        [[0, 0], [2, 0]], [[2, 0], [4, 0]], [[2, 0], [2, 2]]])]
    members = [dict(endpoint=dict(instance=f"d{i}", terminal="p"), net="loop",
                    points=[p, [p[0], p[1]+.5]])
               for i, p in enumerate([[0, 0], [4, 0], [2, 2]])]
    inputs = (None, None, dict(grid=.5, routing={}), members, {}, [])
    reserved = [dict(net="foreign", points=[[-.5, 4], [.5, 4]])]
    monkeypatch.setattr(tracks, "_guides", lambda *args: dict(x=[], y=[2]))
    diagnostics = []
    assert run(inputs, edges, reserved, diagnostics) is None
    assert diagnostics[-1]["candidate_rejections"] == ["target_graph_not_preserved"]


def test_shorter_track_can_respect_no_added_bends_and_no_extra_length():
    record, inputs, _, reserved = case()
    inputs[2]["routing"].update(max_detour=1, max_bends=1)
    assert attempt(record, inputs, reserved, [])


@pytest.mark.parametrize("fault", ["missing_anchor", "gap", "off_grid"])
def test_unverified_initial_graph_is_not_repaired(fault):
    _, inputs, edges, reserved = case()
    if fault == "missing_anchor":
        inputs[3][0]["points"][0] = [99, 99]
    elif fault == "gap":
        edges.pop(0)
    else:
        edges[0]["points"][0] = [10.1, 22]
    diagnostics = []
    assert run(inputs, edges, reserved, diagnostics) is None
    assert diagnostics[-1]["reasons"] == ["free_track_unverified_target_graph"]


def test_input_order_and_names_do_not_change_geometry():
    _, inputs, edges, reserved = case()
    before = run(inputs, edges, reserved, [])
    inputs[3].reverse()
    inputs[5].reverse()
    for m in inputs[3] + inputs[5]:
        m["net"] = "renamed"
    edges.reverse()
    after = run(inputs, edges, reserved, [])
    assert [w["points"] for w in before] == [w["points"] for w in reversed(after)]


def test_finalizer_and_deformation_report_the_actual_method(monkeypatch):
    record, inputs, edges, reserved = case()
    monkeypatch.setattr(TemplateCatalog, "get", lambda self, ref: record)
    # Finalizer sees the independent foreign port region after initial planning.
    planned = dict(rail=deformed(record, inputs))
    report = [dict(net="rail", status="rerouted", reasons=[], terminals=8)]
    finalize_routes(planned, report, dict(rail=inputs[3]), dict(foreign=reserved),
                    inputs[2], inputs[4], inputs[5])
    assert report[0]["method"] == "final_free_track"
    assert report[0]["diagnostics"][-1]["stage"] == "final_net_check"
    # Public routing sees a prior net as a labelled fallback obstacle.
    inputs[3].extend(dict(w, endpoint=dict(port=f"P{i}")) for i, w in enumerate(reserved))
    inputs[2]["routing"].update(ports="route", fallback="stub")
    _, result = route_template(*inputs)
    row = next(r for r in result["nets"] if r["net"] == "rail")
    assert row["method"] == "orthogonal_deformation_free_track"
    assert "transform" not in row
