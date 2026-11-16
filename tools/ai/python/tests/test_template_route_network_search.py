"""Cross-net alternatives retain complete graph and fixed terminal evidence."""

import copy

import pytest

from cadai import template_route_network_search as search
from cadai.circuit_geometry_schema import MATRICES, transform
from cadai.template_route_paths import graph_preserved, path_gaps, wire_paths
from cadai.template_route_repair import repair_paths


def setup(orientation="R0"):
    points = dict(A=[[-4, -4], [-1, -4], [-1, 2], [2, 2]],
                  B=[[0, 0], [0, -5], [-2, -5], [-2, 4]])
    points = {n: [transform(p, orientation) for p in ps] for n, ps in points.items()}
    members = {n: [dict(net=n, endpoint=dict(instance=f"{n}{i}", terminal="p"),
                       points=[p, [p[0]+.5, p[1]]])
                   for i, p in enumerate([ps[0], ps[-1]])] for n, ps in points.items()}
    routes = {n: wire_paths([dict(shape=i, segment=0, points=[a, b])
                             for i, (a, b) in enumerate(zip(ps, ps[1:]))], members[n], n)
              for n, ps in points.items()}
    return (routes, members, dict(grid=.5, clearance=0,
                                 routing=dict(max_detour=1.25, max_bends=1)), {}, [], [])


def edges(paths):
    return [dict(w["template_source"], points=w["points"]) for w in paths]


@pytest.mark.parametrize("orientation", list(MATRICES))
def test_both_fixed_other_net_repairs_fail_but_joint_search_succeeds(orientation):
    args = setup(orientation)
    before = copy.deepcopy(args)
    for net, other in [("A", "B"), ("B", "A")]:
        assert repair_paths(edges(args[0][net]), args[1][net], *args[2:5],
                            args[0][other], []) is None
    diagnostics = []
    result = search.network_paths(*args, diagnostics)
    assert result and diagnostics[-1]["changed_nets"] == ["A", "B"]
    assert diagnostics[-1]["constraints"] >= 2
    for n, ws in result.items():
        anchors = [dict(xy=m["points"][0], endpoint=m["endpoint"]) for m in args[1][n]]
        assert graph_preserved(edges(ws), anchors,
                               len({tuple(p) for w in ws for p in w["points"]}), .5)
        assert not path_gaps(ws, args[1][n], *args[2:5], sum(result.values(), []))
        assert {s["shape"] for w in ws for s in w["template_source"]["chain"]} == {0, 1, 2}
    assert args == before


def test_replans_restart_from_original_and_keep_all_terminals(monkeypatch):
    args = setup()
    real, calls = search.repair_paths, []
    def repair(es, ms, layout, placed, guards, blockers, ds):
        assert es == edges(args[0][ms[0]["net"]])
        assert {(g["net"], tuple(g["xy"])) for g in guards} == {
            (n, tuple(m["points"][0])) for n, rows in args[1].items() for m in rows}
        calls.append(copy.deepcopy(blockers))
        return real(es, ms, layout, placed, guards, blockers, ds)
    monkeypatch.setattr(search, "repair_paths", repair)
    assert search.network_paths(*args, [])
    assert any(len(rows) > 1 for rows in calls)


def test_input_order_does_not_change_search():
    args = setup()
    expected = search.network_paths(*args, [])
    shuffled = copy.deepcopy(args)
    shuffled[0].update({n: list(reversed(ws)) for n, ws in shuffled[0].items()})
    assert search.network_paths(*shuffled, []) == expected


@pytest.mark.parametrize("limit", ["MAX_NETS", "MAX_NODES", "MAX_REPLANS", "MAX_CONSTRAINTS",
                                   "MAX_SEGMENTS", "MAX_TOTAL_SEGMENTS", "MAX_OBSTACLES"])
def test_budget_returns_no_partial_result(monkeypatch, limit):
    args = setup()
    before = copy.deepcopy(args)
    monkeypatch.setattr(search, limit, 0)
    diagnostics = []
    assert search.network_paths(*args, diagnostics) is None
    assert diagnostics[-1]["code"] == "target_network_search_exhausted"
    assert args == before


@pytest.mark.parametrize("bad", ["missing_members", "missing_source", "stub", "port", "pin",
                                 "instance", "wrong_net", "disconnected"])
def test_unverified_or_obstructed_graph_cannot_enter_search(bad):
    args = setup()
    if bad == "missing_members":
        args[1].pop("A")
    elif bad == "missing_source":
        args[0]["A"][0].pop("template_source")
    elif bad == "stub":
        args[0]["A"][0].pop("template_segment")
    elif bad == "port":
        args[5].append(dict(net="external", points=[[-1, 0], [-.5, 0]]))
    elif bad == "pin":
        args[4].append(dict(net="external", xy=[-1, 0]))
    elif bad == "instance":
        args[3]["obstacle"] = dict(bbox=[[-1.5, 0], [-.5, 1]])
    elif bad == "wrong_net":
        args[0]["A"][0]["net"] = "C"
    else:
        args[0]["A"].pop()
    before = copy.deepcopy(args)
    assert search.network_paths(*args, []) is None
    assert args == before


def test_invalid_low_level_candidate_is_rechecked(monkeypatch):
    args = setup()
    monkeypatch.setattr(search, "repair_paths", lambda es, ms, *a: args[0][ms[0]["net"]])
    diagnostics = []
    assert search.network_paths(*args, diagnostics) is None
    assert "network_search_invalid_local_candidate" in diagnostics[-1]["candidate_rejections"]


def test_replan_cap_still_checks_already_queued_complete_candidate(monkeypatch):
    args = setup()
    monkeypatch.setattr(search, "MAX_REPLANS", 5)
    diagnostics = []
    assert search.network_paths(*args, diagnostics)
    assert diagnostics[-1]["replans"] == 5


def test_four_complete_nets_can_commit_together():
    routes, members, layout, placed, pins, outside = setup()
    for old, new in [("A", "C"), ("B", "D")]:
        members[new] = copy.deepcopy(members[old])
        for m in members[new]:
            m["net"] = new
            m["endpoint"]["instance"] = new + m["endpoint"]["instance"][1:]
            m["points"] = [[p[0]+20, p[1]+20] for p in m["points"]]
        shifted = edges(routes[old])
        for e in shifted:
            e["points"] = [[p[0]+20, p[1]+20] for p in e["points"]]
        routes[new] = wire_paths(shifted, members[new], new)
    diagnostics = []
    result = search.network_paths(routes, members, layout, placed, pins, outside, diagnostics)
    assert result and diagnostics[-1]["changed_nets"] == ["A", "B", "C", "D"]
    for n, ws in result.items():
        assert not path_gaps(ws, members[n], layout, placed, pins, sum(result.values(), []))


def test_complete_combination_is_rechecked_before_return(monkeypatch):
    args = setup()
    real = search.path_gaps
    def check(ws, ms, layout, placed, pins, reserved):
        if any(w["net"] == ms[0]["net"] for w in reserved):
            return ["late_failure"]
        return real(ws, ms, layout, placed, pins, reserved)
    monkeypatch.setattr(search, "path_gaps", check)
    diagnostics = []
    assert search.network_paths(*args, diagnostics) is None
    assert "network_search_final_check" in diagnostics[-1]["candidate_rejections"]


def test_conflict_selection_rotates_network_pairs_before_repeating():
    from cadai.template_route_conflicts import select_conflict
    groups = [dict(nets=("A", "B"), count=1, segments=([[0, 0], [1, 0]], [[0, 0], [0, 1]])),
              dict(nets=("B", "C"), count=4, segments=([[0, 0], [1, 0]], [[0, 0], [0, 1]]))]
    assert select_conflict(groups, {("A", "B"): 1, ("B", "C"): 0})["nets"] == ("B", "C")
    assert select_conflict(groups, {("A", "B"): 0, ("B", "C"): 0})["nets"] == ("A", "B")


def test_diagnostics_report_pair_and_net_coverage():
    args = setup()
    diagnostics = []
    assert search.network_paths(*args, diagnostics)
    row = diagnostics[-1]
    assert row["conflict_pairs"] and row["net_replans"]
    assert set(row["net_replans"]) == {"A", "B"}
    assert row["candidate_reuses"] >= 0
