"""Public P2 prepare facade: bounded dispatch, mapping and fail-closed states."""

import copy
import json

import pytest
from template_adapt_fixture import geometry_request, install, reusable, target_case

from cadai.template_catalog import TemplateCatalog
from cadai.template_index import build_index
from cadai.template_locations import template_root
from cadai.template_prepare import prepare_plan
from cadai.template_reuse_build import publish_reusable
from cadai.template_schema import TemplateError
from cadai.template_target import target_topology


def _request(reference, args, *, mode="core"):
    result = {
        "spec": args["spec"],
        "bindings": args["bindings"],
        "template_ref": reference["template_ref"],
        "mapping": args["mapping"],
        "allowed_rule_refs": args["allowed_rule_refs"],
        "options": {"match_mode": mode, "allowed_tiers": ["private"],
                    "return_alternatives": 0},
    }
    return result


def test_core_prepare_dispatch_returns_common_needs_geometry_envelope(tmp_path):
    reference = reusable()
    install(tmp_path, reference)
    args = target_case(reference)
    result = prepare_plan(_request(reference, args), workspace=tmp_path)
    assert result["schema"] == "cad.template.prepare-result.v1"
    assert result["status"] == "needs_geometry"
    assert result["best_match"]["template_ref"] == reference["template_ref"]
    assert result["search"]["complete"] and result["search"]["remaining"] == 0
    assert result["plan"] is None


def test_core_prepare_with_explicit_mapping_and_geometry_is_ready(tmp_path):
    reference = reusable()
    install(tmp_path, reference)
    args = target_case(reference)
    # Build the exact geometry input from the existing read-only v2 adapter.
    from cadai.template_circuit import prepare_template

    prepared = prepare_template(args, workspace=tmp_path)
    result = prepare_plan(_request(reference, args) | {
        "geometry": geometry_request(prepared)["geometry"],
        "layout": geometry_request(prepared)["layout"],
    }, workspace=tmp_path)
    assert result["status"] == "ready_for_creation_prepare"
    assert result["plan"] is not None
    assert result["issues"] == []
    assert result["best_match"]["requirements"] == []


def test_core_prepare_preserves_boundary_residual_until_geometry(tmp_path):
    reference = reusable()
    install(tmp_path, reference)
    args = target_case(reference, extra=True)
    result = prepare_plan(_request(reference, args), workspace=tmp_path)
    assert result["status"] == "needs_geometry"
    assert result["best_match"]["template_ref"] == reference["template_ref"]
    assert result["best_match"]["mapping"] is not None
    assert result["best_match"]["mappings"]


def test_template_prepare_retains_callback_validation_for_native_creation(tmp_path):
    from cadai.circuit_spec_schema import digest
    from cadai.template_circuit import prepare_template

    reference = reusable()
    install(tmp_path, reference)
    args = target_case(reference)
    for master in args["bindings"]["masters"]:
        master["callbacks"] = dict(status="required", extension_ref="test:installed")
    prepared = prepare_template(args, workspace=tmp_path)
    geometry = geometry_request(prepared)
    request = _request(reference, args) | {
        "geometry": geometry["geometry"], "layout": geometry["layout"],
    }
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "ready_for_creation_prepare", result
    preview = result["plan"]["preview"]
    assert preview["plan"]["binding_complete"] is False
    assert preview["plan"]["live_binding_verified"] is False
    assert all(i["code"] == "callback_extension_not_validated" for i in preview["plan"]["issues"])
    assert preview["preview_digest"] == digest(preview["plan"])
    from cadai.template_prepare_schema import serialize_result

    tampered = copy.deepcopy(result)
    altered = tampered["plan"]["preview"]
    altered["plan"].update(issues=[], binding_complete=True)
    altered["preview_digest"] = digest(altered["plan"])
    geo = tampered["plan"]["geometry"]
    geo["plan"]["preview_digest"] = altered["preview_digest"]
    geo["geometry_plan_digest"] = digest(geo["plan"])
    with pytest.raises(TemplateError, match="complete structural"):
        serialize_result(tampered)
    for master in request["bindings"]["masters"]:
        master["callbacks"].pop("extension_ref")
    assert prepare_plan(request, workspace=tmp_path)["status"] != "ready_for_creation_prepare"


@pytest.mark.parametrize("omit", [False, True], ids=["boundary-peripheral", "omit-group"])
def test_verified_adaptation_becomes_eligible_only_after_validation(tmp_path, omit):
    from cadai.template_circuit import prepare_template

    reference = reusable(optional=omit)
    install(tmp_path, reference)
    args = target_case(reference, extra=not omit, omit=omit)
    prepared = prepare_template(args, workspace=tmp_path)
    geometry = geometry_request(prepared)
    request = _request(reference, args) | {
        "geometry": geometry["geometry"], "layout": geometry["layout"],
    }
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "ready_for_creation_prepare", result
    best = result["best_match"]
    use = result["plan"]["template_use"]
    assert best["eligibility"] == "eligible"
    selected = next(r for r in result["candidates"] if r["template_ref"] == best["template_ref"])
    assert selected == best
    assert "verified_adaptation" in best["hard_checks"]
    assert best["rank_features"]["adaptation_cost"] == 1
    assert best["rank_features"]["target_coverage"] == (
        len(use["device_map"]) / len(result["plan"]["spec"]["instances"])
    )
    request["allowed_rule_refs"] = []
    refused = prepare_plan(request, workspace=tmp_path)
    assert refused["status"] == "needs_adaptation"
    assert refused["plan"] is None


def test_multiple_unresolved_adaptations_require_candidate_selection(tmp_path):
    from cadai.template_build import summary
    from cadai.template_reuse_build import content_ref

    reference = reusable()
    other = copy.deepcopy(reference)
    other["granularity"] = "subsystem"
    other["template_ref"] = content_ref(other)
    other["summary"] = summary(other)
    install(tmp_path, reference)
    install(tmp_path, other)
    request = _request(reference, target_case(reference, extra=True))
    request.pop("template_ref")
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "needs_selection", result
    assert result["plan"] is None


def test_core_prepare_malformed_pool_and_missing_reference_fail_closed(tmp_path):
    reference = reusable()
    install(tmp_path, reference)
    args = target_case(reference)
    missing = copy.deepcopy(_request(reference, args))
    missing["template_ref"] = "tpl_" + "0" * 64
    result = prepare_plan(missing, workspace=tmp_path)
    assert result["status"] == "unavailable"
    assert result["reason"] == "reference_unavailable"


def test_v3_publication_rechecks_qualification(tmp_path):
    reference = reusable()
    tampered = copy.deepcopy(reference)
    tampered["source"]["reuse_qualification"]["input_digest"] = "0" * 64
    with pytest.raises(TemplateError, match="qualification|digest"):
        publish_reusable([tampered], tmp_path / "catalog.sqlite3")


def test_prepare_uses_index_and_falls_back_when_index_is_tampered(tmp_path, monkeypatch):
    reference = reusable()
    root = template_root(tmp_path, create=True)
    publish_reusable([reference], root / "catalogs" / "data.sqlite3")
    catalog = TemplateCatalog(workspace=tmp_path)
    entries = catalog._entries()
    record = catalog._read(entries[0][0], reference["template_ref"])
    index_path = catalog.index_path()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    build_index({"snapshot_ref": catalog._last_snapshot.snapshot_ref}, [record], index_path)
    args = target_case(reference)
    request = _request(reference, args) | {
        "options": {"match_mode": "exact", "allowed_tiers": ["private"], "return_alternatives": 0}
    }
    # The fixture is a v3 record and therefore has no exact candidate; use a
    # core request to exercise the actual sidecar path.
    request["options"]["match_mode"] = "core"
    request.pop("template_ref", None)
    request.pop("mapping", None)
    result = prepare_plan(request, workspace=tmp_path)
    assert result["diagnostics"]["index"] == "used"
    value = json.loads(index_path.read_text())
    value["rows"][0]["required_device_count"] += 1
    index_path.write_text(json.dumps(value))
    fallback = prepare_plan(request, workspace=tmp_path)
    assert fallback["diagnostics"]["index"] == "index_fallback"


def test_prepare_index_keeps_uncertain_legacy_records_for_authoritative_matcher(tmp_path):
    from cadai.template_build import publish
    from cadai.template_index import build_index
    reference = reusable()
    # Keep the v2 shape and its terminal-inventory evidence gap; this is the
    # common legacy capture case that must remain inconclusive rather than be
    # eliminated by a device-count/index mismatch.
    legacy = copy.deepcopy(reference)
    legacy["schema_version"] = "cad.circuit.template.v2"
    legacy.pop("reuse_contract", None)
    legacy.pop("schema_version", None)
    legacy["schema_version"] = "cad.circuit.template.v2"
    legacy["topology"]["gaps"] = [{"code": "terminal_inventory_unverified"}]
    legacy["summary"] = {**legacy["summary"], "template_ref": legacy["template_ref"]}
    root = template_root(tmp_path, create=True)
    publish([legacy], root / "catalogs" / "data.sqlite3")
    catalog = TemplateCatalog(workspace=tmp_path)
    entries = catalog._entries()
    stored = catalog._read(entries[0][0], legacy["template_ref"])
    index_path = catalog.index_path()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    build_index({"snapshot_ref": catalog._last_snapshot.snapshot_ref}, [stored], index_path)
    selected, status = catalog.index_candidates(
        {legacy["template_ref"]: stored},
        {"devices": [{"kind": "nmos"}]},
        "exact",
    )
    assert status == "index_fallback" and legacy["template_ref"] in selected


def test_prepare_index_does_not_filter_uncertain_target(tmp_path):
    reference = reusable()
    root = template_root(tmp_path, create=True)
    publish_reusable([reference], root / "catalogs" / "data.sqlite3")
    catalog = TemplateCatalog(workspace=tmp_path)
    entries = catalog._entries()
    stored = catalog._read(entries[0][0], reference["template_ref"])
    index_path = catalog.index_path()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    build_index({"snapshot_ref": catalog._last_snapshot.snapshot_ref}, [stored], index_path)
    args = target_case(reference)
    target = target_topology(args["spec"], args["bindings"])
    target["gaps"] = [{"code": "terminal_inventory_unverified"}]
    selected, status = catalog.index_candidates(
        {reference["template_ref"]: stored}, target, "core"
    )
    assert status == "index_fallback"
    assert selected == {reference["template_ref"]: stored}
