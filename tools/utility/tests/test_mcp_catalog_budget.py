"""M4 budget gate: the normal baseline passes, artificial violations fail."""

from copy import deepcopy

import mcp_catalog_budget as budget_module
from mcp_catalog_measure import measurement


def row(name, description="short", structural=64, filler=0):
    properties = {"payload": {"type": "string", "maxLength": 64 + structural}}
    if filler:
        properties["extra"] = {
            "type": "array", "minItems": 1, "maxItems": 16,
            "items": {"type": "string", "description": "x" * filler},
        }
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": properties,
                        "required": [], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    }


def synthetic_report(canonical, host=None, *, profiles=None, groups=None,
                     unsupported=(), advertised=None):
    host = canonical if host is None else host
    return {
        "method": "sico-mcp-bytes.v1",
        "source": {"commit": "fixture"},
        "layers": {
            "canonical": {"measurement": measurement(canonical)},
            "host": {"measurement": measurement(host)},
        },
        "exposure": {
            "sico_desktop": {
                "measurement": measurement(advertised if advertised is not None else host),
                "host_unsupported": list(unsupported),
            },
        },
        "task_groups": {"groups": groups or []},
        "tool_profiles": {"profiles": profiles or []},
    }


def profile(profile_id, count, bytes_, *, missing=(), unavailable=()):
    return {"id": profile_id, "owner": "sico", "status": "draft", "purpose": "x",
            "scope": "declared", "groups": [], "defaults": [], "tool_count": count,
            "bytes": bytes_, "conditional_count": 0, "recovery_count": 0,
            "operations": [], "digest": "sha256:" + profile_id, "missing": list(missing),
            "unavailable": list(unavailable)}


def baseline():
    canonical = [row("alpha"), row("delta")]
    report = synthetic_report(
        canonical, profiles=[profile("desktop_core", 2, 400)])
    return report, budget_module.freeze(report)


def test_frozen_baseline_passes_and_freeze_is_deterministic():
    report, budget = baseline()
    assert budget_module.check(report, budget)["passed"]
    assert budget_module.freeze(report) == budget


def test_unregistered_over_hard_limit_tool_fails_and_debt_can_register_it():
    report, budget = baseline()
    canonical = [row("alpha"), row("delta"), row("heavy", filler=6000)]
    candidate = synthetic_report(canonical, profiles=budget_profiles(budget))
    result = budget_module.check(candidate, budget)
    assert not result["passed"]
    assert any("hard limit" in error for error in result["errors"])
    # Registering the debt in the same commit passes; growing it does not.
    sized = budget_module.freeze(candidate, budget)
    assert any(entry["tool"] == "heavy" for entry in sized["debt"]["tools"])
    assert budget_module.check(candidate, sized)["passed"]
    grown = synthetic_report([row("alpha"), row("delta"), row("heavy", filler=7000)],
                             profiles=budget_profiles(sized))
    result = budget_module.check(grown, sized)
    assert any("debt grows" in error for error in result["errors"])


def budget_profiles(budget):
    return [profile(profile_id, 2, entry["bytes"])
            for profile_id, entry in budget["profiles"].items()]


def test_description_over_budget_fails():
    candidate = synthetic_report([row("alpha", description="x" * 600), row("delta")])
    budget = budget_module.freeze(candidate)
    result = budget_module.check(candidate, budget)
    assert any("description" in error and "budget" in error for error in result["errors"])
    within = synthetic_report([row("alpha", description="x" * 500), row("delta")])
    assert budget_module.check(within, budget_module.freeze(within))["passed"]


def test_missing_group_and_profile_members_fail_unless_allowed():
    report, budget = baseline()
    report["task_groups"]["groups"] = [
        {"id": "g1", "missing": ["gone"], "conditional_missing": []}]
    report["tool_profiles"]["profiles"] = [
        {**profile("desktop_core", 2, 400), "missing": ["ghost"],
         "unavailable": []}]
    result = budget_module.check(report, budget)
    assert any("group g1" in error for error in result["errors"])
    assert any("desktop_core" in error for error in result["errors"])
    allowed = deepcopy(budget)
    allowed["allowed_missing"] = {"groups": {"g1": ["gone"]},
                                  "profiles": {"desktop_core": ["ghost"]}}
    assert budget_module.check(report, allowed)["passed"]
    # Host-unsupported facts are recorded once and need no allowance.
    report["tool_profiles"]["profiles"] = [
        {**profile("desktop_core", 2, 400), "missing": ["live_x"],
         "unavailable": ["live_x"]}]
    report["task_groups"]["groups"] = []
    assert budget_module.check(report, budget)["passed"]


def test_profile_budget_and_debt_growth_fail():
    report, budget = baseline()
    report["tool_profiles"]["profiles"] = [profile("desktop_core", 2, 60000)]
    result = budget_module.check(report, budget)
    assert any("exceeds budget" in error for error in result["errors"])
    sized = budget_module.freeze(report, budget)
    assert sized["profiles"]["desktop_core"]["status"] == "debt"
    assert budget_module.check(report, sized)["passed"]
    report["tool_profiles"]["profiles"] = [profile("desktop_core", 2, 70000)]
    result = budget_module.check(report, sized)
    assert any("frozen debt" in error for error in result["errors"])


def test_aliases_must_not_be_advertised_or_duplicated():
    # tool_help is a real hidden alias in cadai.mcp_definitions.ALIASES.
    canonical = [row("alpha"), row("tool_help")]
    report, budget = baseline()
    candidate = synthetic_report(canonical, profiles=budget_profiles(budget))
    result = budget_module.check(candidate, budget)
    assert any("aliases advertised" in error for error in result["errors"])
    duplicated = synthetic_report([row("alpha"), row("alpha")],
                                  profiles=budget_profiles(budget))
    result = budget_module.check(duplicated, budget)
    assert any("duplicate advertised" in error for error in result["errors"])


def test_host_unsupported_tools_must_not_be_advertised():
    report, budget = baseline()
    candidate = synthetic_report([row("alpha"), row("live_x")],
                                 profiles=budget_profiles(budget),
                                 unsupported=["live_x"])
    result = budget_module.check(candidate, budget)
    assert any("host-unsupported" in error for error in result["errors"])


def test_inventory_changes_need_a_ledger_entry():
    report, budget = baseline()
    renamed = synthetic_report([row("alpha"), row("gamma")],
                               profiles=budget_profiles(budget))
    result = budget_module.check(renamed, budget)
    assert any("unexplained removed tool delta" in error for error in result["errors"])
    assert any("unexplained added tool gamma" in error for error in result["errors"])
    ledgered = deepcopy(budget)
    ledgered["changes"] = [{"kind": "renamed", "layer": layer,
                            "from": "delta", "to": "gamma",
                            "before_sha256": budget["layers"][layer]["tools"]["delta"],
                            "after_sha256": renamed["layers"][layer]["measurement"]["tools"][1]["content_sha256"],
                            "reason": "fixture rename", "owner": "sico"}
                           for layer in ("canonical", "host")]
    assert budget_module.check(renamed, ledgered)["passed"]
    # A reviewed freeze records the removal; an empty reason must fail until
    # the same commit explains the semantic loss.
    frozen = budget_module.freeze(renamed, budget)
    result = budget_module.check(renamed, frozen)
    assert any("reason must be reviewed" in error for error in result["errors"])
    explained = deepcopy(frozen)
    for entry in explained["removals"]:
        entry["reason"] = "rename reviewed; same contract"
    assert budget_module.check(renamed, explained)["passed"]
    # A ledger entry that no longer matches anything is stale.
    stale = deepcopy(budget)
    stale["changes"] = [{"kind": "changed", "layer": "canonical", "name": "alpha",
                         "reason": "fixture", "owner": "sico"}]
    result = budget_module.check(report, stale)
    assert any("stale change-ledger entry" in error for error in result["errors"])


def test_layer_growth_and_resolved_debt_fail():
    report, budget = baseline()
    bigger = synthetic_report([row("alpha"), row("delta", description="x" * 200)],
                              profiles=budget_profiles(budget))
    result = budget_module.check(bigger, budget)
    assert any("grow beyond frozen" in error or "unexplained changed" in error
               for error in result["errors"])
    # A registered debt that no longer exceeds its limit must be removed.
    heavy = synthetic_report([row("alpha"), row("delta"), row("heavy", filler=6000)],
                             profiles=budget_profiles(budget))
    sized = budget_module.freeze(heavy, budget)
    light = synthetic_report([row("alpha"), row("delta"), row("heavy")],
                             profiles=budget_profiles(sized))
    result = budget_module.check(light, sized)
    assert any("no longer exceeds" in error for error in result["errors"])


def test_repository_budget_has_only_explicit_unexpired_governance_debt():
    import json
    from engineering_debt import assess
    from engineering_findings import mcp_findings
    import mcp_catalog_report

    report = mcp_catalog_report.build()
    budget = budget_module.load_budget(
        budget_module.Path(__file__).resolve().parents[1] / "mcp_catalog_budget.json")
    result = budget_module.check(report, budget)
    root = budget_module.Path(__file__).resolve().parents[3]
    path = root / "tools/utility/engineering_debt.json"
    payload = json.loads(path.read_text())
    payload["entries"] = [r for r in payload["entries"] if r["id"].startswith("mcp:")]
    # This measurement regression also runs in synchronized source-test trees
    # whose Git HEAD is older. History is enforced by engineering_gate/pre-push,
    # not inferred from that installation's unrelated checkout metadata.
    assessment = assess(mcp_findings(report, budget, result), payload, previous=payload,
                        base_commit=payload["established_base"])
    assert not assessment["errors"], assessment["errors"]
