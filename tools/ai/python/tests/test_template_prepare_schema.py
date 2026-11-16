"""Every response state has the same bounded, serializable contract."""

import copy
import json

import pytest
from template_circuit_fixture import geometry_args, persisted_case
from template_search_corpus import pool_item, topology

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_schema import digest
from cadai.template_circuit import prepare_template
from cadai.template_prepare_schema import STATUSES, serialize_result
from cadai.template_schema import TemplateError
from cadai.template_search import search_exact


@pytest.mark.parametrize("status", [s for s in STATUSES if s != "ready_for_creation_prepare"])
def test_all_nonready_states_are_json_serializable(status):
    result = search_exact(topology(), [pool_item()])
    result["status"] = status
    if status == "no_match":
        result["best_match"] = None
    if status in {"inconclusive", "invalid_request", "unavailable"}:
        result["search"]["complete"] = False
    value = serialize_result(result)
    assert json.loads(json.dumps(value)) == value
    value["issues"].clear()
    assert result["issues"]


def test_ready_requires_actual_preview_and_geometry_artifacts(tmp_path, monkeypatch):
    args, record = persisted_case(tmp_path, monkeypatch)
    prepared = prepare_template(args, workspace=tmp_path)
    geometry_request = geometry_args(prepared)
    geometry_request["layout"]["grid"] = 0.0625
    geometry = preview_geometry(**geometry_request, workspace=tmp_path)
    result = search_exact(topology(), [pool_item()])
    result["status"] = "ready_for_creation_prepare"
    best = result["best_match"]
    best["template_ref"] = record["template_ref"]
    best["requirements"] = []
    use = prepared["template_use"]
    best["mapping"] = {
        "devices": {d["source_device"]: d["instance"] for d in use["devices"]},
        "nets": use["net_map"],
        "ports": use["port_map"],
    }
    best["rank_features"]["mapping_digest"] = digest(best["mapping"])
    result["plan"] = {
        "spec": prepared["spec"],
        "template_use": prepared["template_use"],
        "preview": prepared,
        "geometry": geometry,
        "geometry_input": geometry_request["geometry"],
    }
    result["issues"] = []
    assert serialize_result(result)["plan"]["geometry"]["plan"]["creation_ready"] is False
    for fault in (
        "plan_missing",
        "geometry_digest",
        "spec",
        "unchecked_geometry",
        "incomplete",
        "different_candidate_mapping",
        "stale_geometry_input",
    ):
        bad = copy.deepcopy(result)
        if fault == "plan_missing":
            bad["plan"] = None
        elif fault == "geometry_digest":
            bad["plan"]["geometry"]["geometry_plan_digest"] = "0" * 64
        elif fault == "spec":
            bad["plan"]["spec"]["instances"][0]["parameters"]["width"] = "WRONG"
        elif fault == "unchecked_geometry":
            bad["plan"]["geometry"]["plan"]["geometry_checks_passed"] = False
            bad["plan"]["geometry"]["geometry_plan_digest"] = digest(
                bad["plan"]["geometry"]["plan"]
            )
        elif fault == "incomplete":
            bad["search"]["complete"] = False
        elif fault == "different_candidate_mapping":
            bad["best_match"]["mapping"] = copy.deepcopy(bad["best_match"]["mapping"])
            bad["best_match"]["mapping"]["devices"]["d0"] = "WRONG"
            bad["best_match"]["rank_features"]["mapping_digest"] = digest(
                bad["best_match"]["mapping"]
            )
        elif fault == "stale_geometry_input":
            bad["plan"]["geometry_input"]["instances"][0]["master_revision"] = "STALE"
        with pytest.raises(TemplateError):
            serialize_result(bad)


@pytest.mark.parametrize(
    "fault",
    [
        "extra",
        "null_candidates",
        "false_count",
        "nan",
        "no_match",
        "duplicate_alternative",
        "mapping_digest",
        "missing_plan",
    ],
)
def test_malformed_or_contradictory_results_rejected(fault):
    result = search_exact(topology(), [pool_item()])
    if fault == "extra":
        result["confidence"] = 1
    elif fault == "null_candidates":
        result["alternatives"] = None
    elif fault == "false_count":
        result["search"]["examined"] = True
    elif fault == "nan":
        result["diagnostics"]["elapsed_ms"] = float("nan")
    elif fault == "no_match":
        result["status"] = "no_match"
    elif fault == "duplicate_alternative":
        result["alternatives"] = [copy.deepcopy(result["best_match"])]
    elif fault == "mapping_digest":
        result["best_match"]["mapping"]["devices"]["d0"] = "wrong"
    elif fault == "missing_plan":
        result["status"] = "ready_for_creation_prepare"
    with pytest.raises(TemplateError):
        serialize_result(result)
