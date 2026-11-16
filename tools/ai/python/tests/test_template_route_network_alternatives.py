"""Alternative witnesses and geometries retain common checks and hard budgets."""

import copy

import pytest
from test_template_route_network_priority import context

from cadai import template_route_conflicts as conflicts
from cadai import template_route_network_search as search
from cadai.template_route_paths import path_gaps

ROWS = [[[2, 4], [2, -3], [-4, -3], [-4, 0]],
        [[4, 2], [1, 2], [1, -2], [0, -2]],
        [[0, 4], [-5, 4], [-5, -2], [-2, -2]]]


def rotated():
    args = context("R90", rows=ROWS)
    args[2]["routing"].update(max_bends=1, max_detour=1.5)
    return args


def test_dead_pair_does_not_discard_other_conflicts(monkeypatch):
    args = rotated()
    before = copy.deepcopy(args)
    with monkeypatch.context() as limited:
        limited.setattr(search, "MAX_FALLBACK_REPLANS", 0)
        assert search.network_paths(*args, []) is None
    ds = []
    result = search.network_paths(*args, ds)
    assert result and ds[-1]["stalled_pairs"] >= 1
    assert 0 < ds[-1]["fallback_replans"] <= search.MAX_FALLBACK_REPLANS
    assert args == before


def test_distinct_geometries_with_same_constraints_get_separate_states(monkeypatch):
    args = context(rows=[[[-2, -2], [-2, -1], [4, -1], [4, -4]],
                         [[2, -4], [-3, -4], [-3, 2], [4, 2]],
                         [[2, 0], [2, -1], [-4, -1], [-4, 0]]])
    args[2]["routing"].update(max_bends=2, max_detour=1.25)
    pushed, real = [], search.heappush
    def push(queue, item):
        pushed.append(copy.deepcopy(item))
        return real(queue, item)
    monkeypatch.setattr(search, "heappush", push)
    ds = []
    result = search.network_paths(*args, ds)
    assert result and ds[-1]["choice_branches"] > 0
    assert any(a[3] == b[3] and a[2] != b[2] for i, a in enumerate(pushed) for b in pushed[i+1:])
    for n, ws in result.items():
        assert not path_gaps(ws, args[1][n], *args[2:5], sum(result.values(), []))
    assert ds[-1]["candidate_checks"] <= search.MAX_VALIDATIONS


@pytest.mark.parametrize("limit", ["MAX_CHOICES", "MAX_QUEUED", "MAX_TRANSITIONS",
                                   "MAX_VALIDATIONS", "MAX_FALLBACK_REPLANS"])
def test_alternative_budget_failure_is_atomic(monkeypatch, limit):
    args = rotated()
    before = copy.deepcopy(args)
    monkeypatch.setattr(search, limit, 0)
    ds = []
    assert search.network_paths(*args, ds) is None
    assert ds[-1]["code"] == "target_network_search_exhausted"
    assert any("limit" in r for r in ds[-1]["candidate_rejections"])
    assert args == before


def test_witnesses_are_bounded_and_do_not_use_whole_path_bbox(monkeypatch):
    routes = dict(A=[dict(points=[[0, 0], [10, 0]])],
                  B=[dict(points=[[x, -1], [x, 1]]) for x in range(8)])
    monkeypatch.setattr(conflicts, "MAX_WITNESSES", 2)
    rows = conflicts.conflict_groups(routes, 0)
    assert rows[0]["count"] == 8 and len(rows[0]["witnesses"]) == 2
    assert rows[0]["truncated"]
    reverse = {n: [dict(points=list(reversed(w["points"]))) for w in reversed(ws)]
               for n, ws in routes.items()}
    assert conflicts.conflict_groups(reverse, 0) == rows


def test_failure_on_first_witness_attempts_the_next_without_partial_commit(monkeypatch):
    args = context(reuse=True)
    real = search.repair_paths
    calls = []
    def repair(*a):
        calls.append(copy.deepcopy(a[5]))
        # All local attempts deliberately fail; real graph/obstacle checks and
        # collision detection remain active to verify witness fallback control.
        real(*a)
        return None
    monkeypatch.setattr(search, "repair_paths", repair)
    ds = []
    assert search.network_paths(*args, ds) is None
    assert ds[-1]["witness_trials"] > len(ds[-1]["conflict_pairs"])
    assert len({repr(c) for c in calls}) > 2
