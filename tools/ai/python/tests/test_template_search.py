"""Exhaustiveness, unknowns, budget accounting and ranking regressions."""

import copy
import sys

import pytest
from template_search_corpus import DATASET_DIGEST, cases, pool_item, topology

from cadai.template_graph import match
from cadai.template_schema import digest
from cadai.template_search import search_exact


@pytest.mark.parametrize("case", cases(), ids=lambda case: case["id"])
def test_labeled_corpus(case):
    result = search_exact(
        case["target"],
        case["candidates"],
        {"max_query_time_ms": 10000},
        port_maps=case["port_maps"],
    )
    assert result["status"] == case["expected_status"]
    if case["expected_mapping"]:
        assert result["best_match"]["mapping"] == case["expected_mapping"]
    assert result["plan"] is None


def test_corpus_has_independent_family_splits():
    corpus = cases()
    dev = {c["family"] for c in corpus if c["split"] == "development"}
    acceptance = {c["family"] for c in corpus if c["split"] == "acceptance"}
    assert len(corpus) >= 50 and not dev & acceptance
    assert len({c["id"] for c in corpus}) == len(corpus)
    assert digest(corpus) == DATASET_DIGEST


def test_eleventh_candidate_and_alternatives_exclude_best():
    wrong = topology()
    wrong["devices"][0]["kind"] = "resistor"
    pool = [pool_item(wrong, n) for n in range(10)]
    pool.extend(pool_item(number=n) for n in range(10, 14))
    result = search_exact(topology(), pool, {"max_query_time_ms": 10000})
    assert result["search"] == {
        "complete": True,
        "selection_mode": "best",
        "examined": 14,
        "remaining": 0,
        "stop_reason": "exhausted",
    }
    assert result["best_match"]["template_ref"] == pool[10]["template_ref"]
    assert [c["template_ref"] for c in result["alternatives"]] == [
        c["template_ref"] for c in pool[11:13]
    ]


def test_origin_is_tiebreaker_and_pool_order_is_irrelevant():
    pool = [
        pool_item(number=n, origins=[tier])
        for n, tier in enumerate(("builtin", "project", "private"))
    ]
    a, b = [search_exact(topology(), values) for values in (pool, list(reversed(pool)))]
    assert a["best_match"]["origins"] == ["private"]
    assert a["snapshot_ref"] == b["snapshot_ref"]
    a.pop("diagnostics")
    b.pop("diagnostics")
    assert a == b


@pytest.mark.parametrize("found", [True, False])
def test_one_timeout_prevents_best_or_no_match_claim(found):
    def verifier(left, right, *args, **kwargs):
        if left.get("stall"):
            return {"status": "inconclusive", "reason": "search_budget_exceeded", "states": 1}
        return match(left, right, *args, **kwargs)

    uncertain = topology()
    uncertain["stall"] = True
    certain = topology()
    if not found:
        certain["devices"][0]["kind"] = "capacitor"
    result = search_exact(
        topology(), [pool_item(uncertain, 0), pool_item(certain, 1)], matcher=verifier
    )
    assert result["status"] == "inconclusive" and not result["search"]["complete"]
    assert result["search"]["examined"] == 2
    assert result["best_match"]["eligibility"] == ("eligible" if found else "inconclusive")


def test_state_budget_is_global():
    budgets = []

    def verifier(*args, **kwargs):
        budgets.append(kwargs["max_states"])
        return {"status": "different", "states": min(60, kwargs["max_states"])}

    result = search_exact(
        topology(),
        [pool_item(number=i) for i in range(3)],
        {"max_match_states": 100},
        matcher=verifier,
    )
    assert budgets == [100, 40]
    assert result["status"] == "inconclusive"
    assert result["search"]["remaining"] == 1


def test_deadline_checks_post_match_and_reports_overrun():
    now = [0.0]

    def verifier(*args, **kwargs):
        assert kwargs["timeout"] <= 0.05
        now[0] += 0.1
        return {"status": "different", "states": 1}

    result = search_exact(
        topology(), [pool_item()], {"max_query_time_ms": 50}, clock=lambda: now[0], matcher=verifier
    )
    assert result["status"] == "inconclusive"
    assert result["diagnostics"]["budget_overrun_ms"] == pytest.approx(50)


def test_first_eligible_and_fixed_reference_scopes():
    pool = [pool_item(number=i) for i in range(3)]
    result = search_exact(
        topology(), pool, {"selection_mode": "first_eligible", "return_alternatives": 0}
    )
    assert result["status"] == "needs_mapping"
    assert result["search"]["stop_reason"] == "first_eligible"
    assert not result["search"]["complete"] and result["search"]["remaining"] == 2
    fixed = search_exact(
        topology(), pool, {"return_alternatives": 0}, template_ref=pool[2]["template_ref"]
    )
    assert fixed["best_match"]["template_ref"] == pool[2]["template_ref"]
    assert fixed["search"]["complete"] and fixed["search"]["examined"] == 1


@pytest.mark.parametrize(
    "opts",
    [
        {"selection_mode": "first_eligible"},
        {"match_mode": "core"},
        {"return_alternatives": True},
        {"max_query_time_ms": float("nan")},
        {"routing": "template"},
        {"allowed_rule_refs": ["unversioned"]},
        {"allowed_tiers": ["private", "private"]},
        {"max_prepare_time_ms": 5000},
    ],
)
def test_unsupported_or_conflicting_options_are_not_ignored(opts):
    assert search_exact(topology(), [pool_item()], opts)["status"] == "invalid_request"


def test_conflicts_are_checked_before_tier_or_reference_filters():
    a = pool_item(number=1)
    b = copy.deepcopy(a)
    b["origins"] = ["builtin"]
    b["topology"]["devices"][0]["kind"] = "capacitor"
    assert (
        search_exact(topology(), [a, b], {"allowed_tiers": ["private"]})["status"] == "unavailable"
    )
    same = copy.deepcopy(a)
    same["origins"] = ["project"]
    merged = search_exact(topology(), [a, same])
    assert merged["search"]["examined"] == 1
    assert merged["best_match"]["origins"] == ["private", "project"]


def test_malformed_graphs_empty_pool_and_dependency_are_distinct(monkeypatch):
    assert search_exact(topology(), [])["status"] == "unavailable"
    bad = topology()
    bad["devices"].append(bad["devices"][0])
    assert search_exact(bad, [pool_item()])["status"] == "invalid_request"
    assert search_exact(topology(), [pool_item(bad)])["status"] == "unavailable"
    monkeypatch.setitem(sys.modules, "networkx", None)
    result = search_exact(topology(), [pool_item()])
    assert result["status"] == "unavailable"
    assert result["issues"][0]["code"] == "matcher_unavailable"


def test_mapping_input_and_unknown_classes_never_become_matches():
    item = pool_item()
    result = search_exact(topology(), [item], port_maps={item["template_ref"]: {"ABSENT": "IN"}})
    assert result["status"] == "invalid_request"
    item["topology"]["devices"][0]["kind"] = "unknown"
    result = search_exact(item["topology"], [item])
    assert result["status"] == "inconclusive"


def test_overlarge_response_is_an_envelope_without_silent_alternative_truncation(monkeypatch):
    from cadai import template_prepare_schema
    from cadai.template_schema import canonical

    pool = [pool_item(number=i) for i in range(3)]
    result = search_exact(topology(), pool)
    limit = len(canonical(result).encode()) - 100
    monkeypatch.setattr(template_prepare_schema, "MAX_RESPONSE_BYTES", limit)
    result = search_exact(topology(), pool)
    assert result["status"] == "unavailable"
    assert result["issues"][0]["code"] == "response_budget"
    assert len(canonical(result).encode()) <= limit


def test_sorting_remains_inside_global_deadline(monkeypatch):
    from cadai import template_search

    now = [0.0]
    original = template_search.ranking_key

    def delayed_rank(candidate):
        now[0] += 0.2
        return original(candidate)

    monkeypatch.setattr(template_search, "ranking_key", delayed_rank)
    result = search_exact(
        topology(), [pool_item()], {"max_query_time_ms": 50}, clock=lambda: now[0]
    )
    assert result["status"] == "inconclusive"
    assert result["search"]["stop_reason"] == "time_budget"


@pytest.mark.parametrize("key", ["bad\nkey", "bad\u007fkey", "bad\ud800key"])
def test_invalid_text_still_produces_serializable_error(key):
    assert search_exact(topology(), [pool_item()], {key: 1})["status"] == "invalid_request"


@pytest.mark.parametrize("fault", ["array_name", "unknown_scope", "node_limit", "open_pin"])
def test_unsupported_source_facts_stay_unknown(fault):
    source = topology()
    if fault == "array_name":
        source["devices"][0]["source_name"] = "M<3:0>"
    elif fault == "unknown_scope":
        source["nets"][0]["is_global"] = None
    elif fault == "open_pin":
        source["devices"][0]["pins"][0]["net"] = None
    else:
        for index in range(2, 64):
            device = copy.deepcopy(source["devices"][0])
            device["id"] = "d" + str(index)
            device["pins"] = [{"name": "pin" + str(i), "net": "n0"} for i in range(8)]
            source["devices"].append(device)
    result = search_exact(topology(), [pool_item(source)])
    assert result["status"] == "inconclusive"
    if fault == "node_limit":
        assert search_exact(source, [pool_item()])["status"] == "invalid_request"
