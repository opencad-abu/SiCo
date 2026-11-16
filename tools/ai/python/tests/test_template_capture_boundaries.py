"""Installed graphics, hierarchy and archived semantics have distinct proof boundaries."""

import copy

import pytest
from template_extract_fixture import capture_rows, evidence, recount, request
from test_circuit_templates import write_capture
from test_template_extract_reuse import extract

from cadai.template_build import make_template
from cadai.template_capture import read_capture
from cadai.template_catalog import TemplateCatalog
from cadai.template_classification import classify
from cadai.template_schema import TemplateError
from cadai.template_source_qualification import require_qualified


def with_graphic():
    rows = capture_rows()
    graphic = dict(rows[2], name="G0", libName="supplyAlias", cellName="gnd",
                   builtin_library="analogLib", schematic_available=True, terminal_names=[])
    rows[-2:-2] = [graphic]
    views = rows[1]["electrical_observation"]["views"]
    views[0]["instance_count"] += 1
    views.append(dict(views[1], target=["supplyAlias", "gnd", "symbol"]))
    return recount(rows)


@pytest.mark.parametrize("builtin,cell,role", [("analogLib", "gnd", "supply_marker"),
    ("analogLib", "vdd", "supply_marker"), ("basic", "ipin", "port_graphic"),
    ("basic", "noConn", "no_connect")])
def test_only_installed_available_symbol_graphics_precede_schematic(builtin, cell, role):
    instance = dict(capture_rows()[2], builtin_library=builtin, cellName=cell,
                    schematic_available=True)
    assert classify(instance, set())[0] == role
    for change in (dict(builtin_library=None), dict(master_available=False),
                   dict(master_oa_view_type="schematic")):
        assert classify(instance | change, set())[0] == "hierarchy"
    with pytest.raises(TemplateError, match="graphic"):
        classify(instance, set(), evidence("resistor"))
    # Even a verified electrical builtin is not permission to ignore hierarchy.
    custom_view = instance | dict(master_oa_view_type="schematic", schematic_available=False)
    assert classify(custom_view, set())[0] == "hierarchy"
    electrical = instance | dict(builtin_library="analogLib", cellName="res")
    assert classify(electrical, set())[0] == "hierarchy"


def test_aliased_marker_master_is_retained_and_qualified(tmp_path):
    _, _, result = extract(tmp_path, rows=with_graphic())
    assert result["ok"], result
    record = TemplateCatalog(workspace=tmp_path).get(result["template_ref"])
    assert len(record["topology"]["devices"]) == 2
    graphic = record["topology"]["ignored_graphics"][0]
    assert graphic["master"] == dict(library="supplyAlias", cell="gnd", view="symbol")
    assert require_qualified(record)


@pytest.mark.parametrize("fault", ["extra_basic", "missing", "inheritance", "parameterized",
                                  "nested", "count", "oa_view_type"])
def test_ignored_graphics_still_require_complete_safe_electrical_proof(tmp_path, fault):
    rows = with_graphic()
    views = rows[1]["electrical_observation"]["views"]
    if fault == "extra_basic":
        views.append(dict(views[-1], target=["basic", "unobserved", "symbol"]))
    elif fault == "missing":
        views.pop()
    elif fault == "inheritance":
        views[-1]["terminal_expressions"] = ["hidden!"]
    elif fault == "parameterized":
        views[-1]["parameterized"] = True
    elif fault == "nested":
        views[-1]["instance_count"] = 1
    elif fault == "count":
        views[0]["instance_count"] += 1
    else:
        views[-1]["oa_view_type"] = "schematic"
    _, _, result = extract(tmp_path, rows=rows)
    assert not result["ok"] and result["reuse"]["template_ref"] is None


def test_archived_v1_keeps_hierarchy_and_old_graphic_geometry_requirement(tmp_path):
    rows = with_graphic()
    rows[1]["classification_semantics"] = "explicit_evidence_v1"
    for row in rows:
        if row["kind"] == "instance":
            row.pop("master_oa_view_type")
    # v1 collected analog markers' geometry and classified this marker as hierarchy.
    rows[-2:-2] = [dict(kind="instance_geometry", instance="G0", library_path="/builtin",
                       observation=dict(ok=False, code="old", message="old reader"))]
    capture = read_capture(write_capture(tmp_path, recount(rows)))
    record = make_template(capture, classifications=request()["classifications"])
    assert record["rule_version"] == "20260922.classification.v1"
    assert record["topology"]["devices"][0]["role"] == "hierarchy"
    assert not record["topology"]["ignored_graphics"]
    rebuilt = make_template(copy.deepcopy(capture), classifications=request()["classifications"])
    assert rebuilt == record


def test_failed_geometry_preserves_source_cause_without_claiming_qualification(tmp_path):
    rows = capture_rows()
    next(r for r in rows if r["kind"] == "instance_geometry")["observation"] = dict(
        ok=False, code="pdk_body_unavailable", message="body signature exceeds adapter budget")
    _, _, result = extract(tmp_path, rows=rows)
    issue = result["reuse"]["issues"][0]
    assert issue == result["diagnostics"]["issues"][0]
    assert issue["object_ref"] == "M0" and issue["master"]["cell"] == rows[2]["cellName"]
    assert issue["cause_code"] == "pdk_body_unavailable"
    assert issue["message"] == "body signature exceeds adapter budget"
    assert issue["next_action"] == "repair_source_and_recapture"
    assert result["reuse"]["template_ref"] is None
