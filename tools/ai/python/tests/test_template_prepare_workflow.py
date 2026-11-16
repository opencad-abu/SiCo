"""Preparation consumes retained inputs and never bypasses mapping or geometry gates."""

import copy
import json

import pytest
from template_adapt_fixture import geometry_request, install, reusable, target_case
from test_template_prepare_facade import _request

from cadai.circuit_spec_schema import digest
from cadai.mcp import McpServer
from cadai.pdk_binding import PdkBindings
from cadai.template_circuit import prepare_template
from cadai.template_placement import preview_placement
from cadai.template_prepare import prepare_plan
from cadai.template_prepare_schema import serialize_result


@pytest.fixture
def workflow(tmp_path):
    record = reusable()
    install(tmp_path, record)
    args = target_case(record)
    prepared = prepare_template(args, workspace=tmp_path)
    request = _request(record, args) | {
        "geometry": geometry_request(prepared)["geometry"],
        "placement": {"clearance": 0, "stub_length": 0.25, "routing": {"mode": "stub"}},
    }
    return request, prepared


def retained_request(request):
    """Use the real retained resolver with fixed, PDK-neutral observations."""
    adapter = PdkBindings(None)
    for master, geometry in zip(request["bindings"]["masters"], request["geometry"]["instances"]):
        ref = "pdk-adapter:" + digest(master)
        adapter.records[ref] = {
            "master": copy.deepcopy(master), "geometry": copy.deepcopy(geometry),
            "project_ref": request["spec"]["project_ref"],
            "context": dict(session_ref="test:session", session_generation=1,
                            project_ref=request["spec"]["project_ref"], virtuoso_version="test"),
        }
    compact = copy.deepcopy(request)
    for field in ("bindings", "geometry"):
        compact.pop(field)
    compact["spec"].pop("project_ref")
    compact["spec"].pop("binding_snapshot")
    compact["binding_refs"] = list(adapter.records)
    return compact, adapter


def test_integrated_placement_matches_existing_public_workflow(tmp_path, workflow):
    request, prepared = workflow
    before = copy.deepcopy(request)
    placed = preview_placement({
        **{k: request[k] for k in ("spec", "bindings", "geometry", "template_ref")},
        "template_use": prepared["template_use"], "options": request["placement"],
    }, workspace=tmp_path)
    explicit = {k: v for k, v in request.items() if k != "placement"}
    explicit["layout"] = placed["layout"]
    expected = prepare_plan(explicit, workspace=tmp_path)
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == expected["status"] == "ready_for_creation_prepare", result
    assert result["plan"] == expected["plan"]
    assert "next_action" not in result
    assert request == before


def test_retained_inputs_reach_ready_through_mcp(tmp_path, workflow):
    request, _ = workflow
    compact, adapter = retained_request(request)
    before = copy.deepcopy(compact)
    server = McpServer(object(), workspace=tmp_path)
    server._pdk_bindings = adapter
    response = server.handle(dict(jsonrpc="2.0", id=1, method="tools/call", params={
        "name": "prepare_circuit_template_plan", "arguments": compact,
    }))
    assert not response["result"]["isError"], response
    result = json.loads(response["result"]["content"][0]["text"])
    assert result["status"] == "ready_for_creation_prepare", result
    expanded = adapter.resolve_request(compact, include_geometry=True)
    assert result["plan"] == prepare_plan(expanded, workspace=tmp_path)["plan"]
    assert compact == before


def test_standalone_placement_expands_retained_geometry(tmp_path, workflow):
    compact, adapter = retained_request(workflow[0])
    args = {k: compact[k] for k in ("spec", "binding_refs", "template_ref")}
    args.update(template_use=workflow[1]["template_use"], options=compact["placement"])
    # v2 use is binding-bound; reconstruct it against the selected snapshot.
    expanded = adapter.resolve_request(compact, include_geometry=True)
    prepared = prepare_template({**{k: expanded[k] for k in (
        "spec", "bindings", "template_ref", "mapping", "allowed_rule_refs"
    )}, "request_schema": "cad.template.adapt-request.v2"}, workspace=tmp_path)
    args["template_use"] = prepared["template_use"]
    assert preview_placement(args, workspace=tmp_path, pdk_bindings=adapter)["layout"]


@pytest.mark.parametrize("invalid", ["both_layouts", "routing_missing", "bindings", "geometry"])
def test_invalid_input_is_rejected_before_resolving(tmp_path, workflow, invalid):
    compact, adapter = retained_request(workflow[0])
    if invalid == "both_layouts":
        compact["layout"] = geometry_request(workflow[1])["layout"]
    elif invalid == "routing_missing":
        compact["placement"].pop("routing")
    else:
        compact[invalid] = workflow[0][invalid]
    adapter.resolve_request = lambda *a, **k: pytest.fail("invalid request reached resolver")
    result = prepare_plan(compact, workspace=tmp_path, pdk_bindings=adapter)
    assert result["status"] == "invalid_request"
    assert result["plan"] is None


@pytest.mark.parametrize("resolver", [False, True])
def test_expired_refs_return_actionable_common_envelope(tmp_path, workflow, resolver):
    compact, adapter = retained_request(workflow[0])
    adapter.records.clear()
    result = prepare_plan(compact, workspace=tmp_path, pdk_bindings=adapter if resolver else None)
    assert result["status"] == "needs_binding", result
    assert result["plan"] is None
    assert result["issues"][0]["stage"] == "binding"
    assert "bind_pdk_device" in result["next_action"]["message"]
    assert serialize_result(result) == result


@pytest.mark.parametrize("present,expected", [
    ([], [["geometry", "placement"], ["geometry", "layout"]]),
    (["geometry"], [["placement"], ["layout"]]),
    (["placement"], [["geometry"]]),
])
def test_geometry_feedback_only_requests_missing_inputs(tmp_path, workflow, present, expected):
    request = copy.deepcopy(workflow[0])
    for key in ("geometry", "placement"):
        if key not in present:
            request.pop(key)
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "needs_geometry", result
    assert result["next_action"]["input_alternatives"] == expected


def test_geometry_collision_keeps_concrete_objects(tmp_path, workflow):
    request, prepared = workflow
    request.pop("placement")
    request["layout"] = geometry_request(prepared)["layout"]
    for item in request["layout"]["instances"].values():
        item["xy"] = [0, 0]
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "needs_geometry", result
    collision = next(i for i in result["issues"] if i["code"] == "instance_clearance")
    assert collision["stage"] == "geometry"
    objects = json.loads(collision["message"])["instances"]
    assert len(objects) == 2 and set(objects) <= set(request["layout"]["instances"])
    assert result["plan"] is None


def test_binding_metadata_gaps_stop_before_search(tmp_path, workflow, monkeypatch):
    from cadai import template_prepare

    request = workflow[0]
    request["bindings"]["masters"][0]["parameters_complete"] = False
    monkeypatch.setattr(template_prepare, "search_snapshot", lambda *a, **k: pytest.fail("search"))
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "needs_binding"
    assert any(i["code"] == "parameter_metadata_incomplete" for i in result["issues"])


@pytest.mark.parametrize("damage", ["binding_digest", "revision", "parameters", "grid"])
def test_generated_layout_still_requires_complete_geometry_validation(tmp_path, workflow, damage):
    request = workflow[0]
    if damage == "binding_digest":
        request["geometry"]["binding_digest"] = "0" * 64
    elif damage == "revision":
        request["geometry"]["instances"][0]["master_revision"] = "changed"
    elif damage == "parameters":
        request["geometry"]["instances"][0]["parameters_digest"] = "0" * 64
    else:
        request["placement"]["grid"] = 0.125
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "needs_geometry", result
    assert result["plan"] is None and result["issues"]


def test_mapping_ambiguity_blocks_placement_and_names_required_input(
    tmp_path, workflow, monkeypatch
):
    from cadai import template_placement_layout

    request = workflow[0]
    request.pop("mapping")
    monkeypatch.setattr(template_placement_layout, "plan_layout",
                        lambda *a, **k: pytest.fail("place"))
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "needs_mapping", result
    assert result["next_action"]["input_alternatives"] == [["mapping"]]
    assert result["plan"] is None


def test_retained_geometry_is_not_requested_again_after_mapping(tmp_path, workflow):
    compact, adapter = retained_request(workflow[0])
    compact.pop("placement")
    result = prepare_plan(compact, workspace=tmp_path, pdk_bindings=adapter)
    assert result["status"] == "needs_geometry", result
    assert result["next_action"]["input_alternatives"] == [["placement"], ["layout"]]


@pytest.mark.parametrize("omit", [False, True], ids=["boundary", "optional"])
def test_integrated_adaptation_never_grants_rules(tmp_path, monkeypatch, omit):
    from cadai import template_placement_layout

    record = reusable(optional=omit)
    install(tmp_path, record)
    args = target_case(record, extra=not omit, omit=omit)
    prepared = prepare_template(args, workspace=tmp_path)
    request = _request(record, args) | {
        "geometry": geometry_request(prepared)["geometry"],
        "placement": {"clearance": 0, "stub_length": 0.25, "routing": {"mode": "stub"}},
    }
    assert prepare_plan(request, workspace=tmp_path)["status"] == "ready_for_creation_prepare"
    request["allowed_rule_refs"] = []
    monkeypatch.setattr(template_placement_layout, "plan_layout",
                        lambda *a, **k: pytest.fail("place"))
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "needs_adaptation", result
    assert result["plan"] is None


def test_expired_binding_is_a_common_mcp_result(tmp_path, workflow):
    compact, adapter = retained_request(workflow[0])
    adapter.records.clear()
    server = McpServer(object(), workspace=tmp_path)
    server._pdk_bindings = adapter
    response = server.handle(dict(jsonrpc="2.0", id=1, method="tools/call", params={
        "name": "prepare_circuit_template_plan", "arguments": compact,
    }))
    result = json.loads(response["result"]["content"][0]["text"])
    assert result["status"] == "needs_binding" and result["next_action"]


def test_stage_feedback_remains_bounded_with_explicit_omission_count():
    from cadai.template_prepare_feedback import stage_issues

    rows = [{"code": "instance_clearance", "instances": ["I0", "I1"]} for _ in range(128)]
    result = stage_issues(rows, "geometry")
    assert len(result) == 64
    assert result[-1]["code"] == "additional_issues_omitted"
    assert result[-1]["message"] == "65 additional issues omitted"


def test_legacy_exact_use_can_drive_integrated_placement(tmp_path):
    from cadai.template_build import publish

    record = reusable()
    args = target_case(record)
    # A complete legacy v2 record has no core contract; exact mapping retains v1 use.
    record["schema_version"] = "cad.circuit.template.v2"
    record.pop("reuse_contract")
    for device in record["topology"]["devices"]:
        device["observed_property_names"] = []
    from sicostate import project_directory

    root = project_directory(tmp_path, "ai/reference/circuit-templates/catalogs", create=True)
    publish([record], root / "exact.sqlite3")
    request = _request(record, args, mode="exact")
    request["mapping"] = {"devices": args["mapping"]["device_map"],
                          "nets": args["mapping"]["net_map"],
                          "ports": args["mapping"]["port_map"]}
    # Geometry belongs to the target selection, independent of v1/v2 use envelopes.
    request["geometry"] = geometry_request({
        "spec": args["spec"], "plan": {"bindings": args["bindings"]},
        "template_use": {}, "preview_digest": "",
    })["geometry"]
    request["placement"] = {"clearance": 0, "stub_length": 0.25, "routing": {"mode": "stub"}}
    result = prepare_plan(request, workspace=tmp_path)
    assert result["status"] == "ready_for_creation_prepare", result
    assert result["plan"]["template_use"]["schema"] == "cad.circuit.template-use.v1"


def test_followup_fields_do_not_pollute_cached_search(tmp_path, workflow, monkeypatch):
    monkeypatch.setenv("SICO_TEMPLATE_CACHE", "on")
    request = copy.deepcopy(workflow[0])
    request.pop("placement")
    first = prepare_plan(request, workspace=tmp_path)
    first["next_action"]["input_alternatives"].clear()
    ready = prepare_plan(workflow[0], workspace=tmp_path)
    assert ready["status"] == "ready_for_creation_prepare" and "next_action" not in ready
    again = prepare_plan(request, workspace=tmp_path)
    assert again["diagnostics"]["cache"] == "hit"
    assert again["next_action"]["input_alternatives"] == [["placement"], ["layout"]]
