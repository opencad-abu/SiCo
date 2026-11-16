"""Collision components may move free junctions only as atomic graph repairs."""

import copy

import pytest

from cadai import template_route_components as components
from cadai.circuit_geometry_schema import MATRICES, transform
from cadai.template_route_chains import branches, chain_paths
from cadai.template_route_detour import detour_paths
from cadai.template_route_paths import graph_preserved, path_gaps
from cadai.template_route_repair import repair_paths
from cadai.template_route_tracks import track_paths


def case(orientation="R0", shared=False):
    edges = [dict(shape=i, segment=0, points=points) for i, points in enumerate([
        [[-3, 0], [0, 0]], [[3, 0], [0, 0]], [[0, -3], [0, 0]],
    ])]
    members = [dict(net="rail", endpoint=dict(instance=f"D{i}", terminal="p"),
                    points=[point, [point[0], point[1] + .5]])
               for i, point in enumerate([[-3, 0], [3, 0], [0, -3]])]
    layout = dict(grid=.5, clearance=0, routing=dict(max_bends=3, max_detour=2.5))
    reserved = [dict(net="foreign", points=[[-1, 0], [1, 0]])]
    for row in edges + members + reserved:
        row["points"] = [transform(p, orientation) for p in row["points"]]
    if shared:
        members.append(dict(members[0], endpoint=dict(instance="D0", terminal="q")))
    return edges, members, layout, {}, [], reserved


@pytest.mark.parametrize("orientation", list(MATRICES))
@pytest.mark.parametrize("shared", [False, True])
def test_component_moves_free_junction_and_reconnects_all_incident_branches(orientation, shared):
    args = case(orientation, shared)
    before = copy.deepcopy(args)
    for solver in (detour_paths, track_paths, chain_paths):
        assert solver(*args, []) is None
    diagnostics = []
    result = components.component_paths(*args, diagnostics)
    assert result
    assert diagnostics[-1]["code"] == "target_conflict_component_applied"
    assert diagnostics[-1]["junction_moves"]
    assert not path_gaps(result, args[1], args[2], args[3], args[4], args[5])
    anchors = [dict(xy=m["points"][0], endpoint=m["endpoint"]) for m in args[1]]
    repaired = [dict(w["template_source"], points=w["points"]) for w in result]
    assert graph_preserved(repaired, anchors,
                           len({tuple(p) for w in repaired for p in w["points"]}), .5)
    assert repair_paths(*args, []) == result
    assert len(branches(repaired, args[1])) == 3
    assert {tuple(m["points"][0]) for m in args[1]} <= {
        tuple(p) for w in repaired for p in w["points"]}
    assert {s["shape"] for w in result for s in w["template_source"]["chain"]} == {0, 1, 2}
    assert args == before


@pytest.mark.parametrize("budget", ["MAX_STATES", "MAX_SCANS", "MAX_STEPS", "MAX_CHOICES",
                                    "MAX_BRANCHES", "MAX_COMPONENTS", "MAX_UNITS",
                                    "MAX_SEGMENTS", "MAX_OBSTACLES"])
def test_component_budget_fails_atomically(monkeypatch, budget):
    args = case()
    before = copy.deepcopy(args)
    monkeypatch.setattr(components, budget, 0)
    diagnostics = []
    assert components.component_paths(*args, diagnostics) is None
    assert diagnostics[-1]["code"] == "target_conflict_component_exhausted"
    assert args == before


def test_component_requires_complete_initial_graph():
    args = case()
    args[0].pop()
    diagnostics = []
    assert components.component_paths(*args, diagnostics) is None
    assert diagnostics[-1]["reasons"] == ["conflict_component_unverified_target_graph"]


@pytest.mark.parametrize("fault", ["missing_anchor", "off_grid", "shared_cross_device"])
def test_invalid_initial_terminal_evidence_is_rejected(fault):
    args = case(shared=True)
    if fault == "missing_anchor":
        args[1][0]["points"][0] = [90, 90]
    elif fault == "off_grid":
        args[0][0]["points"][0] = [-3.1, 0]
    else:
        args[1][-1]["endpoint"]["instance"] = "other"
    diagnostics = []
    assert components.component_paths(*args, diagnostics) is None
    assert diagnostics[-1]["reasons"] == ["conflict_component_unverified_target_graph"]


def test_pinned_junction_cannot_move():
    args = case()
    args[1].append(dict(net="rail", endpoint=dict(instance="center", terminal="p"),
                        points=[[0, 0], [0, .5]]))
    assert components.component_paths(*args, []) is None


def test_names_and_input_order_do_not_change_geometry():
    args = case(shared=True)
    first = components.component_paths(*args, [])
    args[0].reverse()
    args[1].reverse()
    for m in args[1]:
        m["net"] = "renamed"
    again = components.component_paths(*args, [])
    assert [w["points"] for w in first] == [w["points"] for w in again]


@pytest.mark.parametrize("obstacle", ["body", "pin", "wire"])
def test_joint_candidate_checks_new_obstacles(monkeypatch, obstacle):
    args = case()
    # Restrict motion to the otherwise successful junction at (-1.5, 0).
    monkeypatch.setattr(components, "_states", lambda *a: iter([{(0, 0): (-1.5, 0)}]))
    if obstacle == "body":
        args[3]["block"] = dict(bbox=[[-1.6, .1], [-1.4, .6]])
    elif obstacle == "pin":
        args[4].append(dict(net="third", xy=[-1.5, .5]))
    else:
        args[5].append(dict(net="third", points=[[-1.5, .1], [-1.5, .6]]))
    # Box in both remaining departure directions, leaving no legal junction.
    args[5].append(dict(net="closed", points=[[-1.5, -.5], [-1.5, -.1]]))
    assert components.component_paths(*args, []) is None


@pytest.mark.parametrize("option,value", [("max_bends", 1), ("max_detour", 1)])
def test_joint_candidate_obeys_requested_limits(option, value):
    args = case()
    args[2]["routing"][option] = value
    result = components.component_paths(*args, [])
    if option == "max_detour":
        assert result is None
    else:
        assert result
        edges = [dict(w["template_source"], points=w["points"]) for w in result]
        assert all(len(ps)-2 <= value for _, ps in branches(edges, args[1]))


def test_late_full_graph_failure_cannot_leak_result(monkeypatch):
    args = case()
    before = copy.deepcopy(args)
    real = components.path_gaps
    monkeypatch.setattr(components, "path_gaps", lambda *a: ["late_failure"]
                        if len(a) > 5 else real(*a))
    diagnostics = []
    assert components.component_paths(*args, diagnostics) is None
    assert diagnostics[-1]["reasons"] == ["conflict_component_final_check"]
    assert args == before


@pytest.mark.parametrize("blocked", [False, True])
def test_disjoint_scopes_commit_together_or_rollback_first_success(blocked):
    args = list(case())
    points = [[[-3, 0], [0, 0]], [[0, -3], [0, 0]], [[0, 0], [4, 0]],
              [[4, 0], [5, 0]], [[5, 0], [6, 0]], [[6, 0], [10, 0]],
              [[10, 0], [10, -3]], [[10, 0], [13, 0]]]
    args[0] = [dict(shape=i, segment=0, points=ps) for i, ps in enumerate(points)]
    args[1] = [dict(net="rail", endpoint=dict(instance=f"D{i}", terminal="p"),
                    points=[p, [p[0], p[1]+.5]]) for i, p in enumerate([
                        [-3, 0], [0, -3], [4, 0], [5, 0], [6, 0], [10, -3], [13, 0]])]
    args[5].append(dict(net="foreign2", points=[[9, 0], [11, 0]]))
    if blocked:
        args[5].append(dict(net="closed", points=[[13, -.5], [13, .5]]))
    before = copy.deepcopy(args)
    diagnostics = []
    result = components.component_paths(*args, diagnostics)
    if blocked:
        assert result is None and diagnostics[-1]["candidates"] > 0
    else:
        assert result and diagnostics[-1]["components"] == 2
        assert not path_gaps(result, args[1], args[2], args[3], args[4], args[5])
    assert args == before
