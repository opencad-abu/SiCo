"""A16: preparation cannot outlive its observed package or overall time budget."""

from types import SimpleNamespace

import pytest
from template_adapt_fixture import geometry_request, reusable, target_case

from cadai import template_prepare
from cadai.template_circuit import prepare_template
from cadai.template_reuse_build import publish_reusable
from cadai.template_schema import TemplateUnavailable


@pytest.fixture
def request_package(tmp_path, monkeypatch):
    from cadai import template_locations

    monkeypatch.setattr(template_locations, "builtin_root", lambda: tmp_path / "builtin")
    monkeypatch.setenv("SICO_TEMPLATE_CACHE", "on")
    reference = reusable()
    root = tmp_path / "package"
    path = root / "catalog.sqlite3"
    publish_reusable([reference], path)
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(root))
    args = target_case(reference)
    prepared = prepare_template(args, workspace=tmp_path)
    geometry = geometry_request(prepared)
    request = {key: args[key] for key in (
        "spec", "bindings", "template_ref", "mapping", "allowed_rule_refs"
    )}
    request.update(geometry=geometry["geometry"], layout=geometry["layout"],
                   options={"match_mode": "core", "allowed_tiers": ["project"],
                            "return_alternatives": 0, "max_prepare_time_ms": 5000})
    return request, path


@pytest.mark.parametrize("warm", [False, True], ids=["cold", "cache-hit"])
@pytest.mark.parametrize("phase", ["search_snapshot", "preview_geometry"])
@pytest.mark.parametrize("mutation", ["replace", "remove", "add"])
def test_package_change_during_prepare_never_returns_ready(
    tmp_path, monkeypatch, request_package, warm, phase, mutation
):
    request, path = request_package
    if warm:
        assert template_prepare.prepare_plan(request, workspace=tmp_path)["status"] == (
            "ready_for_creation_prepare"
        )
        if phase == "search_snapshot":
            from cadai.template_cache import cache_for_root

            cache_for_root(tmp_path / ".sico/ai/circuit_templates").plan.clear()
    original = getattr(template_prepare, phase)

    def change_after_read(*args, **kwargs):
        result = original(*args, **kwargs)
        if warm and phase == "search_snapshot":
            assert result["diagnostics"]["cache"] == "hit"
        if mutation == "replace":
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
        elif mutation == "remove":
            path.unlink()
        else:
            shards = path.parent / "catalogs"
            shards.mkdir()
            (shards / "added.sqlite3").write_bytes(path.read_bytes())
        return result

    monkeypatch.setattr(template_prepare, phase, change_after_read)
    result = template_prepare.prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "unavailable"
    assert result["reason"] == "catalog_changed"
    assert result["plan"] is None
    assert result["snapshot_ref"].startswith("snp_")


def test_geometry_overall_budget_is_enforced(tmp_path, monkeypatch, request_package):
    request, _ = request_package
    request["options"]["max_prepare_time_ms"] = 1000
    clock = [100.0]
    monkeypatch.setattr(template_prepare, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original = template_prepare.preview_geometry

    def slow_geometry(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] += 2
        return result

    monkeypatch.setattr(template_prepare, "preview_geometry", slow_geometry)
    result = template_prepare.prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "inconclusive"
    assert result["reason"] == "prepare_budget_exceeded"
    assert result["plan"] is None
    assert result["snapshot_ref"].startswith("snp_")
    assert not result["search"]["complete"]
    assert result["diagnostics"]["elapsed_ms"] == 2000
    assert result["diagnostics"]["budget_overrun_ms"] == 1000


def test_late_template_read_failure_is_a_common_envelope(
    tmp_path, monkeypatch, request_package
):
    request, _ = request_package

    def unavailable(*args, **kwargs):
        raise TemplateUnavailable("catalog member unavailable during geometry")

    monkeypatch.setattr(template_prepare, "preview_geometry", unavailable)
    result = template_prepare.prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "unavailable"
    assert result["plan"] is None


@pytest.mark.parametrize("failure", ["snapshot", "budget"])
def test_integrated_placement_preserves_final_guards(
    tmp_path, monkeypatch, request_package, failure
):
    from cadai import template_placement_layout

    request, path = request_package
    request.pop("layout")
    request["placement"] = {"clearance": 0, "stub_length": 0.25, "routing": {"mode": "stub"}}
    clock = [100.0]
    monkeypatch.setattr(template_prepare, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original = template_placement_layout.plan_layout

    def interrupted(*args, **kwargs):
        result = original(*args, **kwargs)
        if failure == "snapshot":
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
        else:
            clock[0] += 10
        return result

    monkeypatch.setattr(template_placement_layout, "plan_layout", interrupted)
    result = template_prepare.prepare_plan(request, workspace=tmp_path)
    assert result["status"] == ("unavailable" if failure == "snapshot" else "inconclusive")
    assert result["reason"] == ("catalog_changed" if failure == "snapshot"
                                else "prepare_budget_exceeded")
    assert result["plan"] is None
    assert ("next_action" in result) == (failure == "budget")
