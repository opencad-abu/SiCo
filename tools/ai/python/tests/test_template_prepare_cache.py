"""P4 integration: search cache is pure, complete and dependency-bound."""

from __future__ import annotations

import copy
import json
import sqlite3

import pytest
from template_adapt_fixture import install, reusable, target_case

from cadai.template_cache import cache_for_root
from cadai.template_catalog import TemplateCatalog
from cadai.template_prepare import prepare_plan
from cadai.template_schema import TemplateUnavailable


def _request(reference, args, *, mode="core", cache=None):
    return {
        "spec": args["spec"],
        "bindings": args["bindings"],
        "template_ref": reference["template_ref"],
        "mapping": args["mapping"],
        "allowed_rule_refs": args["allowed_rule_refs"],
        "options": {"match_mode": mode, "allowed_tiers": ["private"],
                    "return_alternatives": 0},
    }


def _search_request(reference, args):
    request = _request(reference, args)
    request.pop("template_ref")
    request.pop("mapping")
    return request


def test_prepare_search_cache_second_call_hits_and_returns_defensive_copy(tmp_path):
    reference = reusable()
    install(tmp_path, reference)
    args = target_case(reference)
    request = _search_request(reference, args)

    first = prepare_plan(request, workspace=tmp_path)
    assert first["diagnostics"]["cache"] == "miss"
    second = prepare_plan(request, workspace=tmp_path)
    assert second["diagnostics"]["cache"] == "hit"
    assert first["status"] == second["status"] == "needs_mapping"
    second["best_match"]["requirements"].append("caller_mutation")
    third = prepare_plan(request, workspace=tmp_path)
    assert third["diagnostics"]["cache"] == "hit"
    assert "caller_mutation" not in third["best_match"]["requirements"]


def test_prepare_search_cache_key_changes_with_spec_and_binding_dependencies(tmp_path):
    reference = reusable()
    install(tmp_path, reference)
    args = target_case(reference)
    request = _search_request(reference, args)
    first = prepare_plan(request, workspace=tmp_path)
    assert first["diagnostics"]["cache"] == "miss"

    changed = copy.deepcopy(request)
    changed["bindings"] = copy.deepcopy(request["bindings"])
    changed["bindings"]["source_ref"] = "fixture:p4-changed"
    changed["options"]["max_match_states"] = 100000
    second = prepare_plan(changed, workspace=tmp_path)
    assert second["diagnostics"]["cache"] == "miss"


def test_prepare_cache_can_be_disabled_without_semantic_change(tmp_path, monkeypatch):
    reference = reusable()
    install(tmp_path, reference)
    args = target_case(reference)
    request = _search_request(reference, args)
    enabled = prepare_plan(request, workspace=tmp_path)
    monkeypatch.setenv("SICO_TEMPLATE_CACHE", "off")
    disabled = prepare_plan(request, workspace=tmp_path)
    for value in (enabled, disabled):
        value.pop("diagnostics", None)
    assert enabled == disabled


def test_cache_plan_layer_is_not_populated_by_search_only(tmp_path):
    reference = reusable()
    install(tmp_path, reference)
    args = target_case(reference)
    request = _search_request(reference, args)
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "needs_mapping"
    assert cache_for_root(tmp_path / ".sico/ai/circuit_templates").stats()["plan"]["entries"] == 0


def test_content_cache_hits_only_after_snapshot_is_rechecked(tmp_path):
    reference = reusable()
    install(tmp_path, reference)
    first = TemplateCatalog(workspace=tmp_path).get(reference["template_ref"])
    second = TemplateCatalog(workspace=tmp_path).get(reference["template_ref"])
    assert first == second == reference
    stats = cache_for_root(tmp_path / ".sico/ai/circuit_templates").stats()["content"]
    assert stats["hits"] >= 1 and stats["misses"] >= 1


def test_content_hit_does_not_hide_current_sqlite_summary_drift(tmp_path):
    reference = reusable()
    install(tmp_path, reference)
    catalog = TemplateCatalog(workspace=tmp_path)
    entries = catalog._entries()
    path = entries[0][0]
    ref = reference["template_ref"]
    assert catalog._read(path, ref) == reference
    with sqlite3.connect(path) as db:
        summary = json.loads(db.execute(
            "SELECT summary_json FROM templates WHERE ref=?", (ref,)
        ).fetchone()[0])
        summary["cell"] = "summary-drift"
        db.execute(
            "UPDATE templates SET summary_json=? WHERE ref=?",
            (json.dumps(summary), ref),
        )
    # Deliberately reuse the already fixed snapshot/member binding to exercise
    # the cached-read guard itself; normal public calls build a new snapshot
    # first and therefore miss on the changed member digest.
    with pytest.raises(TemplateUnavailable, match="changed|summary"):
        catalog._read(path, ref)
