"""Core selection must obey public options before producing the result envelope."""

import pytest
from template_search_options_fixture import candidates, core_input

from cadai.template_core_search import search_core


@pytest.mark.parametrize("ambiguous", [False, True])
def test_first_eligible_stops_before_better_origin_without_claiming_best(ambiguous):
    pool = candidates(2)
    pool[0]["origins"], pool[1]["origins"] = ["builtin"], ["private"]
    target, mapping = core_input(pool[0])
    result = search_core(target, list(reversed(pool)), selection_mode="first_eligible",
                         return_alternatives=0, **({} if ambiguous else mapping))
    assert result["status"] == ("needs_mapping" if ambiguous else "needs_geometry")
    assert result["search"] == {"selection_mode": "first_eligible", "complete": False,
                                "stop_reason": "first_eligible", "examined": 1, "remaining": 1}
    assert result["best_match"]["template_ref"] == pool[0]["template_ref"]
    assert result["alternatives"] == []
    assert len(result["diagnostics"]["trace"]) == 1


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_best_honors_alternative_count_and_shared_origin_ranking(count):
    pool = candidates()
    for index, row in enumerate(pool):
        row["origins"] = ["builtin"] if index == 0 else ["private"]
    target, mapping = core_input(pool[0])
    result = search_core(target, list(reversed(pool)), return_alternatives=count, **mapping)
    assert result["status"] == "needs_selection"
    assert result["search"]["complete"]
    assert result["search"]["examined"] == 4
    expected = pool[1:] + pool[:1]
    assert result["best_match"]["template_ref"] == expected[0]["template_ref"]
    assert [row["template_ref"] for row in result["alternatives"]] == [
        row["template_ref"] for row in expected[1:1 + count]
    ]


@pytest.mark.parametrize("options", [
    {"selection_mode": "fast"}, {"selection_mode": None},
    {"return_alternatives": True}, {"return_alternatives": -1},
    {"return_alternatives": 4}, {"return_alternatives": 1.0},
    {"selection_mode": "first_eligible", "return_alternatives": 1},
])
def test_invalid_direct_options_return_invalid_request(options):
    pool = candidates(1)
    target, mapping = core_input(pool[0])
    result = search_core(target, pool, **mapping, **options)
    assert result["status"] == "invalid_request"
    assert result["search"]["examined"] == 0
    assert result["plan"] is None


def test_first_eligible_does_not_stop_on_pending_adaptation():
    pool = candidates(2)
    target, mapping = core_input(pool[0], extra=True)
    result = search_core(target, pool, selection_mode="first_eligible",
                         return_alternatives=0, **mapping)
    assert result["status"] == "needs_selection"
    assert result["search"]["examined"] == 2
    assert result["search"]["stop_reason"] == "exhausted"
    assert result["search"]["complete"]
    assert result["best_match"]["eligibility"] == "needs_input"
    assert result["best_match"]["rank_features"] is None
    assert result["alternatives"] == []


def test_first_eligible_preserves_unknown_and_budget_outcomes():
    pool = candidates(2)
    target, mapping = core_input(pool[0])
    limited = search_core(target, pool, max_states=1, selection_mode="first_eligible",
                          return_alternatives=0, **mapping)
    assert limited["status"] == "inconclusive"
    assert not limited["search"]["complete"]
    assert limited["search"]["stop_reason"] != "first_eligible"
    target["gaps"] = [{"code": "terminal_inventory_unverified"}]
    unknown = search_core(target, pool, selection_mode="first_eligible",
                          return_alternatives=0, **mapping)
    assert unknown["status"] == "inconclusive"
    assert unknown["diagnostics"]["unresolved"] == 2
    assert unknown["search"]["examined"] == 2


def test_first_eligible_no_match_requires_complete_rejection():
    pool = candidates(2)
    target, mapping = core_input(pool[0])
    target["devices"][0]["kind"] = "inductor"
    result = search_core(target, pool, selection_mode="first_eligible",
                         return_alternatives=0, **mapping)
    assert result["status"] == "no_match"
    assert result["search"]["complete"]
    assert result["search"]["examined"] == 2
    assert result["best_match"] is None


@pytest.mark.parametrize("mode", ["best", "first_eligible"])
def test_ranking_deadline_cannot_return_a_completed_selection(monkeypatch, mode):
    from cadai import template_core_search

    pool = candidates(1)
    target, mapping = core_input(pool[0])
    now = [0.0]
    original = template_core_search.ranking_key

    def late_ranking(row):
        now[0] = 2.0
        return original(row)

    monkeypatch.setattr(template_core_search, "ranking_key", late_ranking)
    result = search_core(target, pool, selection_mode=mode, return_alternatives=0,
                         timeout=1, clock=lambda: now[0], **mapping)
    assert result["status"] == "inconclusive"
    assert not result["search"]["complete"]
    assert result["search"]["stop_reason"] == "budget_exhausted"
    assert result["diagnostics"]["budget_overrun_ms"] == 1000
