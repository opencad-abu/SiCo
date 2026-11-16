"""Branch adaptation preserves terminals/junctions while moving unpinned bends."""

import copy

import pytest

from cadai import template_route_chains as chains
from cadai.circuit_geometry_schema import MATRICES, transform
from cadai.template_route_detour import detour_paths
from cadai.template_route_paths import path_gaps
from cadai.template_route_tracks import track_paths


def case(orientation="R0", shared=False):
    def point(p):
        return transform(p, orientation)
    # The collision sits on a bend. One track is pinned by the left terminal;
    # shifting the other track encounters the horizontal foreign barrier.
    edges = [dict(shape=i, segment=0, points=[point(p) for p in ps]) for i, ps in enumerate([
        [[0, 0], [0, 2]], [[0, 2], [4, 2]], [[4, 2], [4, 0]]])]
    members = [dict(net="signal", endpoint=dict(instance=f"d{i}", terminal="p"),
                    points=[point(p), point([p[0], p[1] + .5])])
               for i, p in enumerate([[0, 0], [4, 0]])]
    if shared:
        members.append(dict(members[0], endpoint=dict(instance="d0", terminal="q")))
    reserved = [dict(net="other", points=[point(p) for p in ps]) for ps in [
        [[-.5, 2], [.5, 2]], [[-.5, .5], [4.5, .5]]]]
    return edges, members, dict(grid=.5, clearance=0, routing={}), {}, [], reserved


def test_branch_partition_retains_pinned_degree_two_contacts_and_junctions():
    edges, members, *_ = case()
    edges += [dict(shape=3, segment=0, points=[[0, 2], [-2, 2]])]
    members += [dict(endpoint=dict(instance="d2", terminal="p"), points=[[-2, 2], [-2, 3]])]
    groups = chains.branches(edges, members)
    assert len(groups) == 3
    assert [0, 2] in [p for _, ps in groups for p in (ps[0], ps[-1])]
    members += [dict(endpoint=dict(instance="middle", terminal="p"), points=[[4, 2], [4, 3]])]
    assert len(chains.branches(edges, members)) == 4


@pytest.mark.parametrize("orientation", list(MATRICES))
@pytest.mark.parametrize("shared", [False, True])
def test_branch_can_move_colliding_bends_without_changing_endpoint_identity(orientation, shared):
    args = case(orientation, shared)
    saved = copy.deepcopy(args)
    assert detour_paths(*args, []) is None
    assert track_paths(*args, []) is None
    diagnostics = []
    result = chains.chain_paths(*args, diagnostics)
    assert result
    assert not path_gaps(result, args[1], args[2], args[3], args[4], args[5])
    assert diagnostics[-1]["code"] == "target_branch_chain_applied"
    assert args == saved
    assert all(w["template_source"]["chain"] == [dict(shape=i, segment=0) for i in range(3)]
               for w in result)


@pytest.mark.parametrize("budget", ["MAX_SCANS", "MAX_CHOICES", "MAX_COMBINATIONS",
                                    "MAX_BRANCHES", "MAX_SEGMENTS", "MAX_OBSTACLES"])
def test_search_budget_returns_no_partial_result(monkeypatch, budget):
    args = case()
    monkeypatch.setattr(chains, budget, 0)
    diagnostics = []
    assert chains.chain_paths(*args, diagnostics) is None
    assert diagnostics[-1]["code"] == "target_branch_chain_exhausted"


def test_candidate_corner_count_matches_public_option():
    for limit in (1, 2, 3):
        candidates = chains._candidates([0, 0], [4, 0], .5, dict(x=[-1, 1, 5], y=[-1, 1]), limit)
        assert all(len(p)-2 <= limit for p in candidates)
    assert any(len(p) == 5 for p in candidates)


@pytest.mark.parametrize("fault", ["missing", "shared_cross_device", "gap", "off_grid"])
def test_invalid_initial_graph_is_rejected(fault):
    args = case(shared=True)
    if fault == "missing":
        args[1][0]["points"][0] = [10, 10]
    elif fault == "shared_cross_device":
        args[1][-1]["endpoint"]["instance"] = "different"
    elif fault == "gap":
        args[0].pop(1)
    else:
        args[0][0]["points"][0] = [.1, 0]
    diagnostics = []
    assert chains.chain_paths(*args, diagnostics) is None
    assert diagnostics[-1]["reasons"] == ["branch_chain_unverified_target_graph"]


@pytest.mark.parametrize("obstacle", ["body", "pin", "wire"])
def test_every_candidate_is_checked_against_obstacles(monkeypatch, obstacle):
    args = case()
    monkeypatch.setattr(chains, "_candidates", lambda *a: [[[0, 0], [4, 0]]])
    if obstacle == "body":
        args[3]["obstacle"] = dict(bbox=[[1, -.1], [2, .1]])
    elif obstacle == "pin":
        args[4].append(dict(net="third", xy=[2, 0]))
    else:
        args[5].append(dict(net="third", points=[[2, -.5], [2, .5]]))
    assert chains.chain_paths(*args, []) is None


def test_old_bends_cannot_bypass_requested_budget(monkeypatch):
    args = case()
    args[2]["routing"].update(max_bends=1, max_detour=1)
    # Force only a path above the barrier: length exceeds the branch budget.
    monkeypatch.setattr(chains, "_candidates", lambda *a: [
        [[0, 0], [-10, 0], [-10, 3], [4, 3], [4, 0]]])
    assert chains.chain_paths(*args, []) is None


def test_input_order_and_names_do_not_change_selected_geometry():
    args = case()
    result = chains.chain_paths(*args, [])
    args[0].reverse()
    args[1].reverse()
    for member in args[1]:
        member["net"] = "new_name"
    again = chains.chain_paths(*args, [])
    assert [w["points"] for w in result] == [w["points"] for w in again]


def test_adjacent_branch_is_replaced_together_to_free_a_junction_departure():
    # The descending neighbor occupies the only three-segment escape from the
    # right branch. Its four-segment alternative frees the shared junction.
    from cadai.template_route_paths import graph_preserved

    edges = [dict(shape=i, segment=0, points=p) for i, p in enumerate([
        [[-4, 0], [-4, -1]], [[-4, -1], [0, -1]], [[0, -1], [0, 0]],
        [[0, 0], [0, 2]], [[0, 2], [4, 2]], [[4, 2], [4, 0]]])]
    members = [dict(net="rail", endpoint=dict(instance=f"d{i}", terminal="p"),
                    points=[p, [p[0], p[1]+.5]])
               for i, p in enumerate([[-4, 0], [0, 0], [4, 0]])]
    layout = dict(grid=.5, clearance=0, routing={})
    reserved = [dict(net="other", points=[[1, -1.5], [1, 3]])]
    diagnostics = []
    paths = chains.chain_paths(edges, members, layout, {}, [], reserved, diagnostics)
    assert paths and diagnostics[-1]["branches"] == 2
    assert not path_gaps(paths, members, layout, {}, [], reserved)
    anchors = [dict(xy=m["points"][0], endpoint=m["endpoint"]) for m in members]
    result_edges = [dict(w["template_source"], points=w["points"]) for w in paths]
    assert graph_preserved(result_edges, anchors,
                           len({tuple(p) for w in paths for p in w["points"]}), .5)
