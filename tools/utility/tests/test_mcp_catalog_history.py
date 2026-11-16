"""Contract changes need exact reviews; a fresh freeze cannot erase regressions."""

from copy import deepcopy

import pytest

from mcp_catalog_budget import check, freeze
from mcp_catalog_history import check_history
from test_mcp_catalog_budget import baseline, row, synthetic_report, budget_profiles


def review(budget, report, name, kind="changed"):
    return [dict(layer=layer, kind=kind, name=name, reason="specific contract change", owner="fixture",
                 before_sha256=budget["layers"][layer]["tools"].get(name),
                 after_sha256=next(r["content_sha256"] for r in report["layers"][layer]["measurement"]["tools"] if r["name"] == name))
            for layer in ("canonical", "host")]


def test_old_named_ledger_cannot_authorize_a_second_change():
    _, budget = baseline()
    changed = synthetic_report([row("alpha", description="brief"), row("delta")], profiles=budget_profiles(budget))
    budget["changes"] = review(budget, changed, "alpha")
    assert check(changed, budget)["passed"]
    second = synthetic_report([row("alpha", description="other"), row("delta")], profiles=budget_profiles(budget))
    assert any("unexplained changed tool alpha" in e for e in check(second, budget)["errors"])
    current = deepcopy(budget)
    current["changes"] = review(budget, second, "alpha")
    assert any("explain the new contract" in e for e in check_history(current, budget))
    for entry in current["changes"]:
        entry["reason"] = "second contract change reviewed"
    assert not check_history(current, budget)


def test_added_tool_count_is_explained_by_exact_ledger_but_byte_growth_still_fails():
    _, budget = baseline()
    changed = synthetic_report([row("alpha"), row("delta"), row("new")], profiles=budget_profiles(budget))
    budget["changes"] = review(budget, changed, "new", "added")
    errors = check(changed, budget)["errors"]
    assert errors and all("grow beyond frozen" in e for e in errors)
    forged = deepcopy(budget)
    forged["changes"][0]["after_sha256"] = "wrong"
    assert any("unexplained added tool new" in e for e in check(changed, forged)["errors"])


def test_refreeze_does_not_authorize_budget_or_inventory_reset():
    _, budget = baseline()
    changed = synthetic_report([row("alpha"), row("delta", description="more text")], profiles=budget_profiles(budget))
    assert check(changed, freeze(changed, budget))["passed"]  # Local measurement only.
    assert any("policy reset" in e for e in check_history(freeze(changed, budget), budget))


def test_new_profile_requires_a_reviewed_contract_and_removal_is_forbidden():
    _, budget = baseline()
    current = deepcopy(budget)
    current["profiles"]["mixed_design"] = {
        "count": 2, "bytes": 400, "budget": 1024,
        "status": "ok", "owner": "fixture", "exit": "",
    }
    current.setdefault("profile_contracts", {})["mixed_design"] = {
        "count": 2, "digest": "sha256:mixed", "owner": "fixture",
        "reason": "reviewed mixed-design addition",
    }
    assert not check_history(current, budget)

    unreviewed = deepcopy(current)
    del unreviewed["profile_contracts"]["mixed_design"]
    assert any("requires a reviewed profile_contracts" in e
               for e in check_history(unreviewed, budget))

    removed = deepcopy(budget)
    del removed["profiles"]["desktop_core"]
    assert any("cannot remove profiles" in e
               for e in check_history(removed, budget))


@pytest.mark.parametrize("key", ["description_budget", "class_limits", "allowed_missing", "layers", "removals"])
def test_committed_policy_cannot_be_replaced(key):
    _, budget = baseline()
    current = deepcopy(budget)
    current[key] = {}
    assert any(key in e for e in check_history(current, budget))


def test_profile_scope_same_count_change_requires_new_review():
    report, budget = baseline()
    report["tool_profiles"]["profiles"][0]["digest"] = "digest-v1"
    budget["profile_contracts"] = {"desktop_core": dict(count=2, digest="digest-v1", owner="scope", reason="initial review")}
    assert check(report, budget)["passed"]
    report["tool_profiles"]["profiles"][0]["digest"] = "digest-v2"
    assert any("contract changed" in e for e in check(report, budget)["errors"])
    current = deepcopy(budget)
    current["profile_contracts"]["desktop_core"]["digest"] = "digest-v2"
    assert check_history(current, budget)
    current["profile_contracts"]["desktop_core"]["reason"] = "reviewed new members"
    assert not check_history(current, budget)


def test_embedded_debt_must_not_grow_and_may_be_removed_when_resolved():
    report = synthetic_report([row("heavy", filler=6000)])
    budget = freeze(report)
    current = deepcopy(budget)
    current["debt"]["tools"][0]["bytes"] += 1
    assert any("tool debt" in e for e in check_history(current, budget))
    current["debt"]["tools"] = []
    assert not check_history(current, budget)
    assert any("hard limit" in e for e in check(report, current)["errors"])
