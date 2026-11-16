"""Two coupled free junctions must move within the existing atomic repair budget."""

import copy

import pytest

from cadai import template_route_components as components
from cadai import template_route_motion as motion
from cadai.circuit_geometry_schema import MATRICES, transform
from cadai.template_route_chains import branches
from cadai.template_route_paths import path_gaps
from cadai.template_route_repair import repair_paths


def case(orientation="R0", shared=False):
    points = [[[-3, 0], [0, 0]], [[0, -3], [0, 0]], [[0, 0], [4, 0]],
              [[4, 0], [4, 2]], [[4, 2], [7, 2]], [[4, 2], [4, 5]]]
    edges = [dict(shape=i, segment=0, points=p) for i, p in enumerate(points)]
    members = [dict(net="rail", endpoint=dict(instance=f"D{i}", terminal="p"),
                    points=[p, [p[0], p[1]+.5]])
               for i, p in enumerate([[-3, 0], [0, -3], [7, 2], [4, 5]])]
    reserved = [dict(net="one", points=[[-.5, 0], [.5, 0]]),
                dict(net="two", points=[[3.5, 2], [4.5, 2]])]
    for row in edges + members + reserved:
        row["points"] = [transform(p, orientation) for p in row["points"]]
    if shared:
        members.append(dict(members[0], endpoint=dict(instance="D0", terminal="q")))
    return edges, members, dict(grid=.5, clearance=0, routing={}), {}, [], reserved


@pytest.mark.parametrize("orientation", list(MATRICES))
@pytest.mark.parametrize("shared", [False, True])
def test_two_junctions_require_joint_motion(monkeypatch, orientation, shared):
    args = case(orientation, shared)
    before = copy.deepcopy(args)
    with monkeypatch.context() as limited:
        limited.setattr(motion, "MAX_MOVES", 1)
        assert components.component_paths(*args, []) is None
    diagnostics = []
    result = components.component_paths(*args, diagnostics)
    assert result and diagnostics[-1]["components"] == 1
    assert len(diagnostics[-1]["junction_moves"]) == 2
    assert diagnostics[-1]["motion_units"] == 2
    assert diagnostics[-1]["states"] <= components.MAX_STATES
    assert diagnostics[-1]["scans"] <= components.MAX_SCANS
    assert not path_gaps(result, args[1], args[2], args[3], args[4], args[5])
    edges = [dict(w["template_source"], points=w["points"]) for w in result]
    assert len(branches(edges, args[1])) == 5
    assert repair_paths(*args, []) == result
    assert args == before


@pytest.mark.parametrize("budget", ["MAX_GENERATED", "MAX_FRONTIER", "MAX_MOVES"])
def test_motion_budget_cannot_leak_one_junction_repair(monkeypatch, budget):
    args = case()
    before = copy.deepcopy(args)
    monkeypatch.setattr(motion, budget, 0 if budget == "MAX_FRONTIER" else 1)
    diagnostics = []
    assert components.component_paths(*args, diagnostics) is None
    assert args == before


def test_reordered_and_renamed_inputs_choose_same_geometry():
    args = case(shared=True)
    first = components.component_paths(*args, [])
    args[0].reverse()
    args[1].reverse()
    args[5].reverse()
    for m in args[1]:
        m["net"] = "renamed"
    second = components.component_paths(*args, [])
    assert [w["points"] for w in first] == [w["points"] for w in second]


def test_pinned_second_junction_fails_atomically():
    args = case()
    args[1].append(dict(net="rail", endpoint=dict(instance="pinned", terminal="p"),
                        points=[[4, 2], [4, 2.5]]))
    before = copy.deepcopy(args)
    assert components.component_paths(*args, []) is None
    assert args == before


def test_same_junction_can_move_both_axes_without_overwriting_first_assignment():
    args = case()
    groups = branches(args[0], args[1])
    feedback = {}
    states = motion.motion_states(args[0], groups, set(range(len(groups))),
                                  {tuple(m["points"][0]) for m in args[1]},
                                  [[[-.5, -.5], [.5, .5]]], .5, 0, feedback, max_units=8)
    assert next(states) == {}
    feedback["blocked"] = ((0, 0), (-3, 0))
    first = next(states)
    assert first == {(0, 0): (-1, 0)}
    second = next(states)
    assert second[(0, 0)] == (-1, -1)


@pytest.mark.parametrize("unit_limit", [1, 8])
def test_rejected_endpoint_proposals_are_bounded_and_reported(monkeypatch, unit_limit):
    args = case()
    groups = branches(args[0], args[1])
    feedback, checked = {}, []
    monkeypatch.setattr(motion, "MAX_GENERATED", 1)
    def refused(moved):
        checked.append(moved)
        return False
    states = list(motion.motion_states(args[0], groups, set(range(len(groups))),
                                      {tuple(m["points"][0]) for m in args[1]},
                                      [[[-.5, -.5], [.5, .5]]], .5, 0, feedback,
                                      max_units=unit_limit, allowed=refused))
    assert states == [{}]
    assert len(checked) == 1 and feedback["truncated"]


def test_known_blocked_branch_focus_excludes_unrelated_junction():
    args = case()
    groups = branches(args[0], args[1])
    feedback = {}
    states = motion.motion_states(args[0], groups, set(range(len(groups))),
                                  {tuple(m["points"][0]) for m in args[1]},
                                  [[[-.5, -.5], [.5, .5]]], .5, 0, feedback, max_units=8)
    assert next(states) == {}
    feedback["blocked"] = ((0, 0), (-3, 0))
    assert set(next(states)) == {(0, 0)}


def test_point_obstacle_pruning_uses_common_guards_before_path_enumeration(monkeypatch):
    args = case()
    args[1].append(dict(net="rail", endpoint=dict(instance="fixed", terminal="p"),
                        points=[[0, 0], [0, .5]]))
    calls = []
    monkeypatch.setattr(components, "_candidates", lambda *a: calls.append(a) or [])
    diagnostics = []
    assert components.component_paths(*args, diagnostics) is None
    assert not calls
    assert "template_paths_different_nets_touch" in diagnostics[-1]["candidate_rejections"]
