"""Deletion consequences, peripheral limits and explicit dual permission at public boundaries."""

import copy

import pytest
from template_adapt_fixture import target_case
from template_extract_fixture import CaptureClient, capture_rows, request
from template_reuse_input_fixture import series_capture
from test_template_extract_reuse import extract
from test_template_reuse_input import core_intent

from cadai.circuit_spec_schema import CircuitSpecError
from cadai.template_adaptation import adapt, verify_use
from cadai.template_catalog import TemplateCatalog, call_template
from cadai.template_prepare import prepare_plan
from cadai.template_reuse_schema import validate_record
from cadai.template_schema import TemplateError


def prepared_request(record, **changes):
    target = target_case(record, **changes)
    return {k: target[k] for k in ("spec", "bindings", "template_ref", "mapping")} | dict(
        options=dict(match_mode="core", allowed_tiers=["private"], return_alternatives=0))


def test_series_omission_derives_private_nets_and_port_and_revalidates(tmp_path):
    rows, args = series_capture()
    _, _, result = extract(tmp_path, rows, args)
    assert result["ok"], result
    record = TemplateCatalog(workspace=tmp_path).get(result["template_ref"])
    group = record["reuse_contract"]["optional_groups"][0]
    nets = {n["id"]: n["source_name"] for n in record["topology"]["nets"]}
    assert {nets[n] for n in group["omit"]["remove_nets"]} == {"MID", "BRANCH"}
    assert group["omit"]["remove_ports"] == ["BRANCH"]
    target = target_case(record, omit=True)
    use = adapt(record, target["spec"], target["bindings"], target["mapping"],
                target["allowed_rule_refs"])
    assert len(use["adaptations"][0]["diff"]["removed_devices"]) == 2
    assert verify_use(record, target["spec"], use, target["bindings"]) == use
    broken = copy.deepcopy(record)
    broken["reuse_contract"]["optional_groups"][0]["omit"]["remove_nets"] = []
    with pytest.raises(TemplateError, match="omitted nets"):
        validate_record(broken)
    use["adaptations"][0]["diff"]["removed_ports"] = []
    with pytest.raises(CircuitSpecError, match="independently verified"):
        verify_use(record, target["spec"], use, target["bindings"])


@pytest.mark.parametrize("change", ["add", "omit", "keep"])
@pytest.mark.parametrize("source_permission", [True, False])
def test_public_prepare_requires_source_and_target_permission(tmp_path, change, source_permission):
    rows, args = series_capture()
    args["reuse"]["allow_add_boundary_group"] = source_permission
    args["reuse"]["allow_omit_optional_groups"] = source_permission
    _, _, result = extract(tmp_path, rows, args)
    assert result["ok"], result
    record = TemplateCatalog(workspace=tmp_path).get(result["template_ref"])
    inputs = prepared_request(record, extra=change == "add", omit=change == "omit")
    names = {"add": ["add_boundary_group"], "omit": ["omit_optional_group"], "keep": []}[change]
    inputs["allowed_rules"] = names
    before = copy.deepcopy(inputs)
    response = prepare_plan(inputs, workspace=tmp_path)
    assert response["status"] == ("needs_geometry" if source_permission or change == "keep"
                                   else "needs_adaptation"), response
    if not source_permission and change != "keep":
        assert response["next_action"]["tool"] == "extract_circuit_templates"
        assert response["issues"][0]["code"].endswith("rule_not_allowed")
    assert inputs == before
    inputs["allowed_rules"] = []
    assert prepare_plan(inputs, workspace=tmp_path)["status"] == (
        "needs_geometry" if change == "keep" else "needs_adaptation")


@pytest.mark.parametrize("fault", ["partial", "core_deleted", "closed", "bad_kind", "count"])
def test_public_consumer_cannot_change_protected_graph_or_partial_group(tmp_path, fault):
    rows, args = series_capture()
    _, _, result = extract(tmp_path, rows, args)
    record = TemplateCatalog(workspace=tmp_path).get(result["template_ref"])
    target = prepared_request(record, extra=fault in {"closed", "bad_kind", "count"})
    target["allowed_rules"] = ["omit_optional_group", "add_boundary_group"]
    if fault in {"partial", "core_deleted"}:
        device = next(d for d in record["topology"]["devices"] if d["source_name"] == (
            "R1" if fault == "partial" else "M0"))
        key = target["mapping"]["device_map"][device["id"]]
        target["spec"]["instances"] = [i for i in target["spec"]["instances"] if i["id"] != key]
        target.pop("mapping")
    elif fault == "closed":
        target["spec"]["instances"][-1]["connections"]["PLUS"] = "A"
    elif fault == "bad_kind":
        target["bindings"]["masters"][-1]["classification"]["kind"] = "nmos"
    else:
        for i in range(4):
            target["spec"]["instances"].append(dict(target["spec"]["instances"][-1],
                                                  id="EX" + str(i), name="EX" + str(i)))
    response = prepare_plan(target, workspace=tmp_path)
    assert response["status"] not in {"needs_geometry", "ready_for_creation_prepare"}, response
    assert response["plan"] is None


@pytest.mark.parametrize("fault", ["missing_flag", "whole_fields", "duplicate_member",
                                  "internal_id", "oversize", "unknown_field"])
def test_malformed_intent_rejected_before_capture(tmp_path, fault):
    args = request() | dict(reuse=core_intent())
    intent = args["reuse"]
    if fault == "missing_flag":
        intent.pop("allow_add_boundary_group")
    elif fault == "whole_fields":
        intent["mode"] = "whole"
    elif fault == "duplicate_member":
        intent["core_instances"] *= 2
    elif fault == "internal_id":
        intent["core_devices"] = intent.pop("core_instances")
    elif fault == "oversize":
        intent["optional_groups"] = [dict(id=str(i), instances=[
            str(j) + "X" * 240 for j in range(64)])
                                     for i in range(3)]
    else:
        intent["optional_groups"][0]["omit"] = dict(remove_nets=[], remove_ports=[])
    client = CaptureClient(tmp_path / "spool", capture_rows())
    with pytest.raises(TemplateError):
        call_template("extract_circuit_templates", args, workspace=tmp_path, client=client)
    assert not client.calls


@pytest.mark.parametrize("names,refs", [(["arbitrary"], False), (["rename", "rename"], False),
                                      (["rename"], True)])
def test_invalid_rule_names_or_dual_inputs_fail_before_prepare(names, refs):
    from template_adapt_fixture import reusable

    inputs = prepared_request(reusable())
    inputs["allowed_rules"] = names
    if refs:
        inputs["allowed_rule_refs"] = []
    assert prepare_plan(inputs)["status"] == "invalid_request"


def test_correct_qualified_contract_offline_keeps_original_identity(tmp_path):
    rows, args = series_capture()
    args["reuse"]["allow_omit_optional_groups"] = False
    client, _, result = extract(tmp_path, rows, args)
    catalog = TemplateCatalog(workspace=tmp_path)
    original = catalog.get(result["template_ref"])
    args["reuse"]["allow_omit_optional_groups"] = True
    fixed = call_template("extract_circuit_templates", dict(
        template_ref=result["template_ref"], reuse=args["reuse"]), workspace=tmp_path)
    assert fixed["ok"] and fixed["live"] is False
    assert fixed["template_ref"] != result["template_ref"]
    assert catalog.get(result["template_ref"]) == original and len(client.calls) == 1
    assert call_template("extract_circuit_templates", dict(
        template_ref=fixed["template_ref"], reuse=args["reuse"]), workspace=tmp_path)[
            "template_ref"] == fixed["template_ref"]
    from pathlib import Path

    Path(fixed["capture"]).write_text("damaged")
    with pytest.raises(TemplateError):
        call_template("extract_circuit_templates", dict(
            template_ref=fixed["template_ref"], reuse=args["reuse"]), workspace=tmp_path)


def test_boundary_declaration_does_not_authorize_geometry_or_minimum_relaxation(tmp_path):
    intent = core_intent()
    intent["boundary_terminals"][0]["min_additional_connections"] = 1
    _, _, result = extract(tmp_path, args=request() | dict(reuse=intent))
    assert result["ok"]
    record = TemplateCatalog(workspace=tmp_path).get(result["template_ref"])
    target = target_case(record, omit=True)
    with pytest.raises(CircuitSpecError, match="boundary_connection_count"):
        adapt(record, target["spec"], target["bindings"], target["mapping"],
              target["allowed_rule_refs"])
