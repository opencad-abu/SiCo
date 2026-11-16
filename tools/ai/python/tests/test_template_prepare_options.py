"""Public core options retain search evidence through adaptation, geometry and cache."""

import copy

import pytest
from template_adapt_fixture import geometry_request, install, target_case
from template_search_options_fixture import candidates, prepare_request

from cadai.template_circuit import prepare_template
from cadai.template_prepare import prepare_plan
from cadai.template_reuse_build import publish_reusable


@pytest.mark.parametrize("ready", [False, True])
def test_prepare_first_eligible_retains_early_stop_through_geometry(tmp_path, ready):
    pool = candidates(2)
    for row in pool:
        install(tmp_path, row)
    request = prepare_request(pool[0])
    request["options"]["selection_mode"] = "first_eligible"
    if ready:
        prepared = prepare_template(target_case(pool[0]), workspace=tmp_path)
        geometry = geometry_request(prepared)
        request.update({key: geometry[key] for key in ("geometry", "layout")})
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == ("ready_for_creation_prepare" if ready else "needs_geometry")
    assert result["search"] == {"selection_mode": "first_eligible", "complete": False,
                                "stop_reason": "first_eligible", "examined": 1, "remaining": 1}
    assert result["best_match"]["template_ref"] == pool[0]["template_ref"]
    assert result["alternatives"] == []
    assert (result["plan"] is not None) == ready


@pytest.mark.parametrize("count", [0, 1, 2, 3])
@pytest.mark.parametrize("extra", [False, True])
def test_prepare_alternative_count_does_not_bypass_selection(tmp_path, count, extra):
    pool = candidates()
    for row in pool:
        install(tmp_path, row)
    request = prepare_request(pool[0], extra=extra)
    request["options"]["return_alternatives"] = count
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "needs_selection", result
    assert result["search"]["complete"]
    assert len(result["alternatives"]) == count
    assert result["plan"] is None
    if extra:
        assert result["best_match"]["rank_features"] is None
    request["template_ref"] = pool[0]["template_ref"]
    request["options"]["return_alternatives"] = 0
    selected = prepare_plan(request, workspace=tmp_path)
    assert selected["status"] == "needs_geometry"
    assert selected["search"]["examined"] == 1
    assert selected["alternatives"] == []
    rank = selected["best_match"]["rank_features"]
    assert rank["adaptation_cost"] == int(extra)
    assert rank["target_coverage"] == (3 / 4 if extra else 1)


def test_core_tier_filter_and_fixed_reference_limit_search(tmp_path, monkeypatch):
    private, project = candidates(2)
    install(tmp_path, private)
    root = tmp_path / "project-templates"
    publish_reusable([project], root / "catalogs" / "templates.sqlite3")
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(root))
    request = prepare_request(project)
    request["options"]["allowed_tiers"] = ["project"]
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "needs_geometry"
    assert result["search"]["examined"] == 1
    assert result["best_match"]["template_ref"] == project["template_ref"]
    assert result["best_match"]["origins"] == ["project"]
    request["template_ref"] = private["template_ref"]
    refused = prepare_plan(request, workspace=tmp_path)
    assert refused["status"] == "unavailable"
    assert refused["plan"] is None


def test_core_search_cache_isolated_by_options_and_never_caches_early_stop(tmp_path):
    pool = candidates()
    for row in pool:
        install(tmp_path, row)
    request = prepare_request(pool[0])
    request.pop("mapping")
    for count in (0, 3):
        request["options"]["return_alternatives"] = count
        first = prepare_plan(request, workspace=tmp_path)
        second = prepare_plan(request, workspace=tmp_path)
        assert first["diagnostics"]["cache"] == "miss"
        assert second["diagnostics"]["cache"] == "hit"
        assert len(second["alternatives"]) == count
    early = copy.deepcopy(request)
    early["options"].update(selection_mode="first_eligible", return_alternatives=0)
    for _ in range(2):
        result = prepare_plan(early, workspace=tmp_path)
        assert result["diagnostics"]["cache"] == "miss"
        assert result["search"]["stop_reason"] == "first_eligible"
        assert result["search"]["examined"] == 1
