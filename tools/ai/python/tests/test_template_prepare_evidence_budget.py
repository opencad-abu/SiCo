"""Placement evidence and phase/transport budgets survive the public prepare envelope."""

from types import SimpleNamespace

import pytest
from test_template_prepare_workflow import workflow as workflow

from cadai import template_placement_layout, template_prepare, template_relations
from cadai.template_prepare_schema import serialize_result


def test_missing_source_geometry_is_visible_in_successful_prepare(tmp_path, workflow, monkeypatch):
    original = template_relations.relations

    def missing_box(record):
        view = original(record)
        view["gaps"].append(dict(code="bounding_box_missing", device="d0"))
        return view

    monkeypatch.setattr(template_relations, "relations", missing_box)
    result = template_prepare.prepare_plan(workflow[0], workspace=tmp_path)
    assert result["status"] == "ready_for_creation_prepare", result
    evidence = result["diagnostics"]["placement"]
    assert any(g["code"] == "bounding_box_missing" for g in evidence["gaps"])
    assert result["issues"] == []  # Nonblocking drawing evidence stays distinct from blockers.
    relations = result["diagnostics"]["relations"]
    assert relations["coverage"]
    assert any(g["code"] == "bounding_box_missing" for g in relations["gaps"])
    assert serialize_result(result) == result


def test_placement_diagnostics_are_bounded_and_planner_version_invalidates_cache(
    tmp_path, workflow, monkeypatch
):
    original = template_placement_layout.plan_layout

    def many_gaps(*args, **kwargs):
        layout, report = original(*args, **kwargs)
        report["gaps"] = [dict(code="terminal_axis_unavailable", instance="Id0", axis="x")
                          for _ in range(100)]
        return layout, report

    monkeypatch.setattr(template_placement_layout, "plan_layout", many_gaps)
    result = template_prepare.prepare_plan(workflow[0], workspace=tmp_path)
    evidence = result["diagnostics"]["placement"]
    assert evidence["gap_count"] == 100
    assert len(evidence["gaps"]) == 8 and evidence["omitted"] == 92
    monkeypatch.setattr(template_placement_layout, "plan_layout", original)
    from cadai import template_prepare_plan_cache

    monkeypatch.setattr(template_prepare_plan_cache, "PLANNER_VERSION", "test:changed")
    again = template_prepare.prepare_plan(workflow[0], workspace=tmp_path)
    assert again["diagnostics"]["placement"]["gap_count"] != 100


@pytest.mark.parametrize("phase", ["relations", "placement"])
def test_exhausted_phase_budget_prevents_starting_next_stage(
    tmp_path, workflow, monkeypatch, phase
):
    clock = [100.0]
    monkeypatch.setattr(template_prepare, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    module = template_relations if phase == "relations" else template_placement_layout
    name = "relations" if phase == "relations" else "plan_layout"
    original = getattr(module, name)

    def slow(*args, **kwargs):
        value = original(*args, **kwargs)
        clock[0] += 10
        return value

    monkeypatch.setattr(module, name, slow)
    next_module = template_placement_layout if phase == "relations" else template_prepare
    next_name = "plan_layout" if phase == "relations" else "preview_geometry"
    monkeypatch.setattr(next_module, next_name, lambda *a, **k: pytest.fail("started next stage"))
    result = template_prepare.prepare_plan(workflow[0], workspace=tmp_path)
    assert result["status"] == "inconclusive" and result["plan"] is None
    assert result["reason"] == "prepare_budget_exceeded"


def test_oversized_ready_plan_returns_common_budget_failure(tmp_path, workflow, monkeypatch):
    from cadai import template_prepare_schema

    monkeypatch.setattr(template_prepare_schema, "MAX_RESPONSE_BYTES", 6000)
    result = template_prepare.prepare_plan(workflow[0], workspace=tmp_path)
    assert result["status"] == "unavailable" and result["plan"] is None
    assert result["reason"] == "response_budget"
    assert result["snapshot_ref"].startswith("snp_")
    assert serialize_result(result) == result


def test_relation_work_budget_is_not_reported_as_a_catalog_change(tmp_path, workflow, monkeypatch):
    def exceeded(record):
        raise template_relations.RelationEvidenceUnavailable(
            "relations_work_budget", "relations work budget exceeded: instances")

    monkeypatch.setattr(template_relations, "relations", exceeded)
    result = template_prepare.prepare_plan(workflow[0], workspace=tmp_path)
    assert result["status"] == "needs_geometry" and result["plan"] is None
    assert result["issues"][0]["code"] == "relations_work_budget"
    assert result["search"]["complete"]


def test_response_budget_failure_still_verifies_the_catalog(tmp_path, workflow, monkeypatch):
    from cadai import template_prepare_schema
    from cadai.template_catalog import TemplateCatalog
    from cadai.template_schema import TemplateUnavailable

    original = template_prepare.preview_geometry

    def changed_after_geometry(*args, **kwargs):
        result = original(*args, **kwargs)

        def changed(self):
            raise TemplateUnavailable("catalog changed after geometry")

        monkeypatch.setattr(TemplateCatalog, "_verify_snapshot", changed)
        return result

    monkeypatch.setattr(template_prepare, "preview_geometry", changed_after_geometry)
    monkeypatch.setattr(template_prepare_schema, "MAX_RESPONSE_BYTES", 6000)
    result = template_prepare.prepare_plan(workflow[0], workspace=tmp_path)
    assert result["status"] == "unavailable" and result["plan"] is None
    assert result["reason"] == "catalog_changed"


def test_final_diagnostic_growth_cannot_escape_response_budget(tmp_path, workflow, monkeypatch):
    from cadai import template_prepare_schema
    from cadai.circuit_spec_schema import canonical
    from cadai.template_prepare_result import finish_result

    result = template_prepare.prepare_plan(workflow[0], workspace=tmp_path)
    result["diagnostics"]["elapsed_ms"] = 0
    limit = len(canonical(result).encode())
    monkeypatch.setattr(template_prepare_schema, "MAX_RESPONSE_BYTES", limit)
    catalog = SimpleNamespace(_last_snapshot=SimpleNamespace(snapshot_ref=result["snapshot_ref"]),
                              _verify_snapshot=lambda: None)
    options = template_prepare_schema.search_options(workflow[0]["options"],
                                                     workflow[0]["template_ref"])
    final = finish_result(result, catalog, 0, options, clock=lambda: 1.23456789)
    assert final["status"] == "unavailable" and final["plan"] is None
    assert final["reason"] == "response_budget"
    assert len(canonical(final).encode()) <= limit
