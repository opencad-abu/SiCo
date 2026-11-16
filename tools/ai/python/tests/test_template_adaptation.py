"""P2 acceptance A01-A07: complete target preservation and fail-closed edits."""

import copy

import pytest
from template_adapt_fixture import geometry_request, install, reusable, target_case
from test_template_circuit_transport import NoLiveClient, rpc, unpack

from cadai.circuit_create import payload
from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_schema import CircuitSpecError
from cadai.mcp import McpServer
from cadai.template_adaptation import adapt, verify_use
from cadai.template_circuit import prepare_template
from cadai.template_core_match import match_core
from cadai.template_core_search import search_core
from cadai.template_reuse_build import make_reusable
from cadai.template_rules import verify_installed_rule_digests
from cadai.template_schema import TemplateError
from cadai.template_target import target_topology


def prepared_use(ref, args):
    return adapt(ref, args["spec"], args["bindings"], args["mapping"], args["allowed_rule_refs"])


def test_a01_a02_a06_fourth_device_and_new_bias_survive_public_creation_payload(tmp_path):
    ref = reusable()
    install(tmp_path, ref)
    args = target_case(ref, extra=True)
    original = copy.deepcopy(args)
    target = target_topology(args["spec"], args["bindings"])
    matched = search_core(target, [ref], timeout=2)
    assert matched["candidates"][0]["status"] in {"matched", "needs_mapping", "needs_adaptation"}
    assert matched["candidates"][0]["mappings"]
    server = McpServer(NoLiveClient(), workspace=tmp_path)
    prepared = unpack(rpc(server, "prepare_template_circuit", args))
    assert prepared["spec"] == args["spec"]
    assert prepared["template_use"]["residual_instances"] == ["EXTRA"]
    assert prepared["template_use"]["boundary_map"][0]["target_endpoint"] == {
        "instance": "EXTRA",
        "terminal": "PLUS",
    }
    geometry = geometry_request(prepared)
    checked = unpack(rpc(server, "preview_circuit_geometry", geometry))
    assert checked["plan"]["geometry_checks_passed"], checked["plan"]["issues"]
    native = payload(
        {**geometry, "geometry_plan_digest": checked["geometry_plan_digest"]}, workspace=tmp_path
    )
    assert len(native[1]) == 4
    extra = next(i for i in native[1] if i[0] == "EXTRA")
    assert dict(extra[6]) == {"PLUS": "OUT", "MINUS": "BIAS"}
    assert "BIAS" in {p[0] for p in native[2]}
    assert args == original


@pytest.mark.parametrize(
    "fault",
    [
        "swap_dg",
        "short_nets",
        "remove_core",
        "remove_bulk",
        "closed_net",
        "wrong_type",
        "missing_classification",
        "missing_rule",
        "rule_digest",
        "extra_unrelated_port",
    ],
)
def test_a03_a07_invalid_target_or_rule_never_creates_partial_use(fault):
    ref = reusable()
    args = target_case(ref, extra=True)
    first = next(i for i in args["spec"]["instances"] if i["id"] == "Id0")
    if fault == "swap_dg":
        first["connections"]["D"], first["connections"]["G"] = (
            first["connections"]["G"],
            first["connections"]["D"],
        )
    elif fault == "short_nets":
        args["mapping"]["net_map"]["n0"] = args["mapping"]["net_map"]["n1"]
    elif fault == "remove_core":
        args["mapping"]["device_map"].pop("d0")
    elif fault == "remove_bulk":
        first["connections"].pop("B")
    elif fault == "closed_net":
        next(i for i in args["spec"]["instances"] if i["id"] == "EXTRA")["connections"]["PLUS"] = (
            "TAIL"
        )
    elif fault == "wrong_type":
        args["bindings"]["masters"][-1]["classification"]["kind"] = "inductor"
    elif fault == "missing_classification":
        args["bindings"]["masters"][0].pop("classification")
    elif fault == "missing_rule":
        args["allowed_rule_refs"] = []
    elif fault == "rule_digest":
        args["allowed_rule_refs"][1]["implementation_digest"] = "0" * 64
    else:
        args["spec"]["ports"].append({"name": "BAD", "net": "TAIL", "direction": "input"})
    with pytest.raises(CircuitSpecError):
        prepared_use(ref, args)


def test_a04_symmetric_inputs_require_explicit_polarity_mapping():
    ref = reusable()
    args = target_case(ref)
    target = target_topology(args["spec"], args["bindings"])
    result = match_core(ref, target, timeout=2)
    assert result["status"] == "needs_mapping"
    assert result["reason"] == "ambiguous_mapping"
    assert len(result["mappings"]) == 2
    explicit = match_core(ref, target, port_map=args["mapping"]["port_map"], timeout=2)
    assert explicit["status"] == "matched" and explicit["complete"]
    assert len(explicit["mappings"]) == 1
    assert prepared_use(ref, args)["verification"]["gaps"] == []


def test_a05_whole_optional_group_omission_and_required_device_rejection():
    ref = reusable(optional=True)
    args = target_case(ref, omit=True)
    use = prepared_use(ref, args)
    assert use["adaptations"][0]["rule_ref"]["id"] == "omit_optional_group"
    assert use["adaptations"][0]["diff"]["removed_devices"] == ["d3"]
    assert use["adaptations"][0]["diff"]["removed_ports"] == ["BIAS"]
    assert use["residual_instances"] == []
    assert verify_use(ref, args["spec"], use, args["bindings"]) == use
    args["mapping"]["omitted_groups"] = []
    with pytest.raises(CircuitSpecError, match="mapping"):
        prepared_use(ref, args)


def test_retained_optional_group_is_a_valid_complete_mapping():
    ref = reusable(optional=True)
    args = target_case(ref)
    use = prepared_use(ref, args)
    assert use["residual_instances"] == []
    assert use["device_map"]["d3"] == "Id3"


def test_explicit_terminal_swap_is_rejected_even_when_target_is_symmetric():
    ref = reusable()
    args = target_case(ref)
    args["mapping"]["terminal_map"]["d0"]["D"], args["mapping"]["terminal_map"]["d0"]["G"] = (
        "G", "D"
    )
    with pytest.raises(CircuitSpecError, match="terminal"):
        prepared_use(ref, args)


@pytest.mark.parametrize("field", ["target", "use", "boundary", "residual", "verification", "rule"])
def test_all_downstream_previews_recompute_adaptation_evidence(tmp_path, field):
    ref = reusable()
    install(tmp_path, ref)
    prepared = prepare_template(target_case(ref, extra=True), workspace=tmp_path)
    args = geometry_request(prepared)
    use = args["template_use"]
    if field == "target":
        next(i for i in args["spec"]["instances"] if i["id"] == "EXTRA")["connections"]["PLUS"] = (
            "TAIL"
        )
    elif field == "use":
        use["net_map"]["n0"] = "BIAS"
    elif field == "boundary":
        use["boundary_map"] = []
    elif field == "residual":
        use["residual_instances"] = []
    elif field == "verification":
        use["verification"]["checked_invariants"].pop()
    else:
        use["adaptations"][0]["rule_ref"]["implementation_digest"] = "0" * 64
    with pytest.raises(CircuitSpecError):
        preview_geometry(**args, workspace=tmp_path)


def test_unknown_and_exhausted_core_search_are_inconclusive():
    ref = reusable()
    args = target_case(ref)
    target = target_topology(args["spec"], args["bindings"])
    assert match_core(ref, target, max_states=1)["status"] == "inconclusive"
    target["devices"][0]["kind"] = "unknown"
    assert match_core(ref, target)["status"] == "inconclusive"


def test_v2_request_never_accepts_legacy_fields(tmp_path):
    ref = reusable()
    install(tmp_path, ref)
    args = target_case(ref)
    args["devices"] = []
    with pytest.raises(CircuitSpecError, match="choose one input source"):
        prepare_template(args, workspace=tmp_path)


def test_a13_geometry_change_recomputes_digest_and_off_grid_fails(tmp_path):
    ref = reusable()
    install(tmp_path, ref)
    prepared = prepare_template(target_case(ref), workspace=tmp_path)
    geometry = geometry_request(prepared)
    baseline = preview_geometry(**geometry, workspace=tmp_path)
    rotated = copy.deepcopy(geometry)
    rotated["layout"]["instances"]["Id0"]["orientation"] = "MY"
    changed = preview_geometry(**rotated, workspace=tmp_path)
    assert changed["geometry_plan_digest"] != baseline["geometry_plan_digest"]
    off_grid = copy.deepcopy(geometry)
    off_grid["layout"]["instances"]["Id0"]["xy"][0] += 0.03125
    with pytest.raises(CircuitSpecError, match="off grid"):
        preview_geometry(**off_grid, workspace=tmp_path)


def test_a14_residual_template_path_fails_safe_when_source_attachment_is_unavailable(tmp_path):
    ref = reusable()
    install(tmp_path, ref)
    prepared = prepare_template(target_case(ref, extra=True), workspace=tmp_path)
    geometry = geometry_request(prepared)
    geometry["layout"]["routing"] = {
        "mode": "end_to_end",
        "engine": "template",
        "fallback": "reject",
    }
    result = preview_geometry(**geometry, workspace=tmp_path)
    assert not result["plan"]["geometry_checks_passed"]
    assert any(issue["code"] == "template_routing_failed" for issue in result["plan"]["issues"])


def test_v3_writer_requires_explicit_source_qualification_evidence():
    ref = reusable()
    base = copy.deepcopy(ref)
    base["source"].pop("electrical_observation", None)
    classifications = {
        d["id"]: {
            "kind": d["kind"],
            "attributes": {},
            "source_ref": "fixture:type",
            "revision": "fixture:v1",
        }
        for d in base["topology"]["devices"]
    }
    with pytest.raises(TemplateError, match="electrical observation"):
        make_reusable(base, base["reuse_contract"], classifications)


def test_v3_qualification_is_recomputed_after_publication_input_changes():
    ref = reusable()
    tampered = copy.deepcopy(ref)
    tampered["source"]["electrical_observation"]["views"][0]["hierarchy_count"] += 1
    with pytest.raises(TemplateError, match="qualification|instance count"):
        from cadai.template_source_qualification import require_qualified

        require_qualified(tampered)


def test_v3_qualification_rejects_fake_empty_terminal_geometry():
    base = reusable()
    base["assets"]["schematic"]["wire_reference"]["geometry"][0]["terminals"] = []
    classifications = {
        d["id"]: {
            "kind": d["kind"],
            "attributes": {},
            "source_ref": "fixture:type",
            "revision": "fixture:v1",
        }
        for d in base["topology"]["devices"]
    }
    with pytest.raises(TemplateError, match="geometry|qualification"):
        make_reusable(base, base["reuse_contract"], classifications)


def test_installed_rule_digest_table_matches_reviewed_handlers():
    assert verify_installed_rule_digests()
