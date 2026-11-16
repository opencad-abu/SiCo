"""Actual multi-net geometry demonstrates conflict selection and candidate reuse."""

import copy
from itertools import permutations

import pytest

from cadai import template_route_network_search as search
from cadai.circuit_geometry_schema import MATRICES, transform
from cadai.template_route_paths import path_gaps, wire_paths


def context(orientation="R0", *, reuse=False, names=("A", "B", "C"), rows=None):
    rows = rows or ([[[0, 0], [0, 3], [4, 3], [4, -4]],
             [[-4, 2], [5, 2], [5, 0], [2, 0]],
             [[-2, 4], [-2, 1], [-4, 1], [-4, -2]]] if reuse else
            [[[-2, -2], [-2, 3], [4, 3], [4, 2]],
             [[0, 0], [0, 3], [-4, 3], [-4, -2]],
             [[2, 0], [1, 0], [1, 4], [0, 4]]])
    points = {n: [transform(p, orientation) for p in ps] for n, ps in zip(names, rows)}
    members = {n: [dict(net=n, endpoint=dict(instance=f"{n}{i}", terminal="p"),
                       points=[p, [p[0]+.5, p[1]]]) for i, p in enumerate([ps[0], ps[-1]])]
               for n, ps in points.items()}
    routes = {n: wire_paths([dict(shape=i, segment=0, points=[a, b])
                             for i, (a, b) in enumerate(zip(ps, ps[1:]))], members[n], n)
              for n, ps in points.items()}
    return routes, members, dict(grid=.5, clearance=0,
                                 routing=dict(max_bends=2, max_detour=1)), {}, [], []


@pytest.mark.parametrize("orientation", list(MATRICES))
@pytest.mark.parametrize("names", list(permutations(("A", "B", "C"))))
def test_sparse_conflict_selection_recovers_with_two_replans(monkeypatch, orientation, names):
    args = context(orientation, names=names)
    before = copy.deepcopy(args)
    monkeypatch.setattr(search, "MAX_REPLANS", 2)
    diagnostics = []
    result = search.network_paths(*args, diagnostics)
    assert result and diagnostics[-1]["replans"] == 2
    assert diagnostics[-1]["changed_nets"] == [names[0]]
    for n, ws in result.items():
        assert not path_gaps(ws, args[1][n], *args[2:5], sum(result.values(), []))
    assert args == before


def test_lexical_first_conflict_exhausts_same_budget(monkeypatch):
    args = context()
    monkeypatch.setattr(search, "MAX_REPLANS", 2)
    monkeypatch.setattr(search, "select_conflict", lambda groups, visits:
                        min(groups, key=lambda r: r["nets"]))
    diagnostics = []
    assert search.network_paths(*args, diagnostics) is None
    assert diagnostics[-1]["reasons"] == ["network_search_replan_limit"]


def test_reuses_complete_candidate_under_new_accumulated_constraints(monkeypatch):
    args = context(reuse=True)
    calls, checks = [], []
    real_repair, real_gaps = search.repair_paths, search.path_gaps
    def repair(*a):
        result = real_repair(*a)
        calls.append((a[1][0]["net"], result))
        return result
    def check(ws, *a):
        checks.append((id(ws), copy.deepcopy(a[-1])))
        return real_gaps(ws, *a)
    monkeypatch.setattr(search, "repair_paths", repair)
    monkeypatch.setattr(search, "path_gaps", check)
    diagnostics = []
    result = search.network_paths(*args, diagnostics)
    assert result and diagnostics[-1]["candidate_reuses"] > 0
    assert len(calls) == diagnostics[-1]["replans"]
    # The same complete path is checked against distinct blocker sets; reuse
    # never relies on the obstacle set from the candidate's first generation.
    assert any(len({repr(b) for obj, b in checks if obj == id(ws)}) > 1
               for _, ws in calls if ws)
    for n, ws in result.items():
        assert not path_gaps(ws, args[1][n], *args[2:5], sum(result.values(), []))


@pytest.mark.parametrize("orientation", list(MATRICES))
def test_another_conflict_enables_path_when_first_pair_has_no_local_child(orientation):
    args = context(orientation, rows=[[[2, 4], [2, -3], [-4, -3], [-4, 0]],
                                     [[4, 2], [1, 2], [1, -2], [0, -2]],
                                     [[0, 4], [-5, 4], [-5, -2], [-2, -2]]])
    args[2]["routing"].update(max_bends=1, max_detour=1.5)
    before = copy.deepcopy(args)
    diagnostics = []
    result = search.network_paths(*args, diagnostics)
    assert result and len(diagnostics[-1]["conflict_pairs"]) == 2
    for n, ws in result.items():
        assert not path_gaps(ws, args[1][n], *args[2:5], sum(result.values(), []))
    assert args == before
