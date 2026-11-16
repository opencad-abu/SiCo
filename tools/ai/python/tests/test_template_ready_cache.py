"""Ready-cache hits must skip planning while preserving all current input guards."""

import copy

import pytest
from test_template_prepare_workflow import retained_request
from test_template_prepare_workflow import workflow as workflow

from cadai import template_placement_layout, template_prepare, template_prepare_plan_cache
from cadai.template_cache import cache_for_root
from cadai.template_prepare_schema import PrepareResponseBudget


def cache(tmp_path):
    return cache_for_root(tmp_path / ".sico/ai/circuit_templates")


def test_hit_skips_search_and_placement_but_revalidates_geometry(tmp_path, workflow, monkeypatch):
    request = workflow[0]
    first = template_prepare.prepare_plan(request, workspace=tmp_path)
    assert first["diagnostics"]["plan_cache"] == "miss"
    monkeypatch.setattr(template_prepare, "search_snapshot",
                        lambda *a, **k: pytest.fail("search repeated"))
    monkeypatch.setattr(template_placement_layout, "plan_layout",
                        lambda *a, **k: pytest.fail("placement repeated"))
    second = template_prepare.prepare_plan(request, workspace=tmp_path)
    assert second["diagnostics"]["plan_cache"] == "hit"
    assert first["plan"] == second["plan"]
    second["plan"]["spec"]["instances"].clear()
    assert template_prepare.prepare_plan(request, workspace=tmp_path)["plan"] == first["plan"]
    original = template_prepare.preview_geometry

    def reject(*a, **k):
        value = original(*a, **k)
        value["plan"]["geometry_checks_passed"] = False
        return value

    monkeypatch.setattr(template_prepare, "preview_geometry", reject)
    with pytest.raises(pytest.fail.Exception, match="search repeated"):
        template_prepare.prepare_plan(request, workspace=tmp_path)


@pytest.mark.parametrize("dependency", [
    "geometry", "placement", "rules", "mapping", "spec", "bindings", "options", "version",
])
def test_changed_dependency_cannot_reuse_ready(tmp_path, workflow, monkeypatch, dependency):
    request = copy.deepcopy(workflow[0])
    template_prepare.prepare_plan(request, workspace=tmp_path)
    if dependency == "geometry":
        request["geometry"]["instances"][0]["master_revision"] = "changed"
    elif dependency == "placement":
        request["placement"]["clearance"] = 0.25
    elif dependency == "rules":
        request["allowed_rule_refs"] = []
    elif dependency == "mapping":
        request.pop("mapping")
    elif dependency == "spec":
        request["spec"]["target"]["cell"] = "changed"
    elif dependency == "bindings":
        request["bindings"]["source_ref"] = "changed"
    elif dependency == "options":
        request["options"]["max_query_time_ms"] = 1000
    else:
        monkeypatch.setattr(template_prepare_plan_cache, "PLANNER_VERSION", "test:new")
    misses = cache(tmp_path).stats()["plan"]["misses"]
    result = template_prepare.prepare_plan(request, workspace=tmp_path)
    assert cache(tmp_path).stats()["plan"]["misses"] == misses + 1
    assert result["diagnostics"].get("plan_cache") != "hit"


def test_retained_binding_geometry_is_resolved_before_cache_lookup(tmp_path, workflow):
    compact, adapter = retained_request(workflow[0])
    first = template_prepare.prepare_plan(compact, workspace=tmp_path, pdk_bindings=adapter)
    assert first["status"] == "ready_for_creation_prepare"
    next(iter(adapter.records.values()))["geometry"]["master_revision"] = "changed"
    again = template_prepare.prepare_plan(compact, workspace=tmp_path, pdk_bindings=adapter)
    assert again["status"] == "needs_geometry"
    assert again["plan"] is None


def test_disabled_cache_preserves_ready_plan(tmp_path, workflow, monkeypatch):
    first = template_prepare.prepare_plan(workflow[0], workspace=tmp_path)
    monkeypatch.setenv("SICO_TEMPLATE_CACHE", "off")
    second = template_prepare.prepare_plan(workflow[0], workspace=tmp_path)
    assert first["plan"] == second["plan"]
    assert second["diagnostics"]["cache"] == "disabled"


def test_cache_write_diagnostics_cannot_overflow_a_valid_response(tmp_path, workflow, monkeypatch):
    result = template_prepare.prepare_plan(workflow[0], workspace=tmp_path)
    original = template_prepare_plan_cache.serialize_result

    def bounded(value):
        if value["diagnostics"].get("plan_cache") == "evicted":
            raise PrepareResponseBudget("diagnostics exceed response budget")
        return original(value)

    monkeypatch.setattr(template_prepare_plan_cache, "serialize_result", bounded)
    monkeypatch.setattr(cache(tmp_path), "put", lambda *a, **k: "evicted")
    assert template_prepare_plan_cache.store_ready(cache(tmp_path), "plan:test", result) == result
