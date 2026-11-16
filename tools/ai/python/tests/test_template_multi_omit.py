"""Bounded multi-group selection composes old rules and protects the whole graph."""

import copy

import pytest
from template_adapt_fixture import geometry_request, install, reusable, target_case

from cadai.circuit_spec_schema import CircuitSpecError
from cadai.template_adaptation import adapt, verify_use
from cadai.template_circuit import prepare_template
from cadai.template_core_match import match_core
from cadai.template_prepare import prepare_plan
from cadai.template_reuse_build import make_reusable
from cadai.template_rules import verify_installed_rule_digests
from cadai.template_schema import TemplateError
from cadai.template_target import target_topology


def prepare(record, request):
    return adapt(record, request["spec"], request["bindings"], request["mapping"],
                 request["allowed_rule_refs"])


@pytest.mark.parametrize("count", [2, 8])
def test_whole_groups_removed_together_through_public_prepare(tmp_path, count):
    record = reusable(optional=count)
    groups = [g["id"] for g in record["reuse_contract"]["optional_groups"]]
    request = target_case(record, omit=groups)
    install(tmp_path, record)
    prepared = prepare_template(request, workspace=tmp_path)
    geometry = geometry_request(prepared)
    result = prepare_plan({k: v for k, v in request.items() if k != "request_schema"} | {
        "geometry": geometry["geometry"], "layout": geometry["layout"],
        "options": {"match_mode": "core", "allowed_tiers": ["private"],
                    "return_alternatives": 0},
    }, workspace=tmp_path)
    assert result["status"] == "ready_for_creation_prepare", result
    use = result["plan"]["template_use"]
    assert [a["site"] for a in use["adaptations"]] == [[g] for g in groups]
    assert len(use["adaptations"][0]["diff"]["removed_devices"]) == count
    assert verify_use(record, request["spec"], use, request["bindings"]) == use
    assert verify_installed_rule_digests()  # old immutable single-group identity remains valid
    assert "OUT" in {n["name"] for n in result["plan"]["spec"]["nets"]}
    assert all(not n["name"].startswith("BIAS") for n in result["plan"]["spec"]["nets"])


@pytest.mark.parametrize("fault", ["duplicate", "undeclared", "partial", "permission", "mix"])
def test_illegal_multi_group_requests_rejected(fault):
    record = reusable(optional=2)
    request = target_case(record, omit=["load", "load1"], extra=fault == "mix")
    if fault == "duplicate":
        request["mapping"]["omitted_groups"] = ["load", "load"]
    elif fault == "undeclared":
        request["mapping"]["omitted_groups"] = ["load", "missing"]
    elif fault == "partial":
        request["mapping"]["omitted_groups"] = ["load"]
    elif fault == "permission":
        request["allowed_rule_refs"] = []
    with pytest.raises((CircuitSpecError, TemplateError)):
        prepare(record, request)


@pytest.mark.parametrize("fault", ["site", "diff", "duplicate_proof"])
def test_multi_group_receipt_cannot_be_forged(fault):
    record = reusable(optional=2)
    request = target_case(record, omit=["load", "load1"])
    use = prepare(record, request)
    if fault == "site":
        use["adaptations"][1]["site"] = ["load"]
    elif fault == "diff":
        use["adaptations"][1]["diff"]["removed_nets"] = []
    else:
        use["adaptations"].append(copy.deepcopy(use["adaptations"][0]))
    with pytest.raises((CircuitSpecError, TemplateError)):
        verify_use(record, request["spec"], use, request["bindings"])


def test_cross_group_internal_net_must_be_declared_as_core_boundary():
    record = reusable(optional=2)
    record["topology"]["devices"][-1]["pins"][1]["net"] = "n_extra"
    classifications = {d["id"]: d["classification_evidence"]
                       for d in record["topology"]["devices"]}
    with pytest.raises(TemplateError, match="undeclared core boundary"):
        make_reusable(record, record["reuse_contract"], classifications)


def test_matcher_does_not_silently_accept_duplicate_selection():
    record = reusable(optional=2)
    request = target_case(record, omit=["load", "load1"])
    result = match_core(record, target_topology(request["spec"], request["bindings"]),
                        omitted_groups=["load", "load"])
    assert result["status"] == "invalid_request" and not result["complete"]
