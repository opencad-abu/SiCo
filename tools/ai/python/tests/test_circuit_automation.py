"""Backend metadata retention and automatic device support across the creation workflow."""

import json
from copy import deepcopy

import pytest
from pdk_binding_fixture import fixture, tagged

from cadai.circuit_create import call_create
from cadai.circuit_generic import call_generic
from cadai.circuit_spec_schema import CircuitSpecError
from cadai.pdk_extensions import DeviceSupportUnavailable, discover_extensions
from sico.adapters.circuit import circuit_summary


def selection(parameter_count=160, callback=True):
    bridge, service, args = fixture()
    service.body_reader = lambda _: dict(
        occupied_bbox=[[0, -1], [1, 1]], pin_centers=[["A", [[0, 0]]]], signature="boundary-pin"
    )
    parameters = bridge.data["cdf"]["parameters"]["items"]
    first = parameters[0]
    parameters.extend(dict(deepcopy(first), name=tagged("extra_" + str(i)))
                      for i in range(parameter_count - 1))
    if callback:
        first["callback"] = tagged('error("discovery must never execute this")')
    found = service.session.call("search_pdk_devices", {"library": "synthetic"})
    args.update(snapshot_ref=found["context"]["snapshot_ref"],
                device_ref=found["items"][0]["device_ref"])
    args["revision"] = service.session.call("get_pdk_device", {
        k: args[k] for k in ("snapshot_ref", "device_ref")
    })["device"]["revision"]
    service.extension_reader = lambda _: {"items": [["installed:support", "v1", True]]}
    return bridge, service, args


def selected_request(ref):
    return dict(binding_refs=[ref], spec=dict(
        schema="cad.circuit.spec.v1", kind="circuit",
        target=dict(library="work", cell="top", view="schematic"),
        instances=[dict(id="M1", name="MP", master="device", role="device", parameters={"w": "1u"},
                        connections={"A": "n1"}, unconnected={})],
        nets=[dict(name="n1", scope="local")], ports=[],
    ))


@pytest.mark.parametrize("discovery_churn", [False, True])
def test_complete_metadata_survives_compact_response_through_native_prepare(discovery_churn):
    _, service, args = selection()
    compact = service.bind({**args, "response_mode": "compact"})
    full = service.bind(args)
    assert compact["binding_ref"] == full["source_ref"]
    assert compact["parameter_count"] == 160
    assert "master" not in compact and "installed:support" not in json.dumps(compact)
    assert len(json.dumps(compact)) < 2000
    request = selected_request(compact["binding_ref"])
    preview = call_generic("preview_circuit_spec", request, pdk_bindings=service)
    assert preview["geometry_preview_ready"] and not preview["plan"]["creation_ready"]
    assert {i["code"] for i in preview["plan"]["issues"]} == {"callback_extension_not_validated"}
    request.update(preview_digest=preview["preview_digest"], layout=dict(
        instances={"M1": dict(xy=[0, 0], orientation="R0")}, ports={}, anchor_choices={},
        grid=0.0625, stub_length=0.25, clearance=0.125,
        routing={"mode": "stub", "decision": {"source": "user", "chosen": "stub"}},
    ))
    geometry = call_generic("preview_circuit_geometry", request, pdk_bindings=service)
    assert geometry["next_action"] == "prepare_circuit_creation"
    request.update(task_ref="task:test", request_id="prepare",
                   geometry_plan_digest=geometry["geometry_plan_digest"])
    if discovery_churn:
        for _ in range(12):
            service.session.call("search_pdk_devices", {})
    seen = []
    validate_prepare = service.validate_prepare

    def checked(expanded):
        assert len(expanded["bindings"]["masters"][0]["parameters"]) == 160
        assert expanded["bindings"]["source_ref"] == "session:synthetic"
        seen.append(expanded)
        return validate_prepare(expanded)

    service.validate_prepare = checked

    class Client:
        def call(self, method, inputs):
            assert method == "eval_skill_native"
            assert inputs["code"].startswith("aiCreatePrepare(")
            assert '"installed:support"' in inputs["code"]
            return True, {"value": '{"ok":true,"prepare_ref":"create-prepare:prepare"}'}

    before = deepcopy(request)
    assert call_create("prepare_circuit_creation", request, Client(), pdk_bindings=service)[0]
    assert len(seen) == 1
    assert request == before and "bindings" not in request
    assert "preview_digest" in circuit_summary("preview_circuit_spec", preview)


def test_circuit_summary_echoes_routing_and_placement_acceptance_numbers():
    geometry = {
        "next_action": "prepare_circuit_creation",
        "geometry_plan_digest": "d" * 64,
        "plan": {
            "routing": {
                "mode": "end_to_end",
                "engine": "cadence_route",
                "edges": 4,
                "pg_style": "stub",
                "pg_stub_terminals": 3,
                "metrics": {"terminals": 9, "routed_terminals": 5, "end_to_end_ratio": 0.5556},
                "fallbacks": [{"endpoint": {"instance": "R0"}, "reason": "no_clear_path"}],
                "junctions": [{"xy": [0, 0], "degree": 3}],
                "interior_escapes": [],
            }
        },
    }
    summary = json.loads(circuit_summary("preview_circuit_geometry", geometry))
    assert summary["routing"]["metrics"]["routed_terminals"] == 5
    assert summary["routing"]["fallbacks"] == 1
    assert summary["routing"]["junctions"] == 1
    assert summary["routing"]["pg_style"] == "stub"
    assert summary["routing"]["engine"] == "cadence_route"
    assert summary["routing"]["edges"] == 4
    placement = json.loads(circuit_summary("preview_template_placement", {
        "report": {
            "rows": [{"members": ["R0", "R2"]}],
            "mirrored_pairs": [],
            "spacing": {"mode": "loose", "column_gap": 1.25},
            "ports": {"left": ["IN", "VSS"], "right": ["OUT"]},
            "gaps": [{"code": "instance_without_row"}],
            "geometry_review": [],
        },
        "placement_digest": "a" * 64,
    }))
    assert placement["rows"] == [["R0", "R2"]]
    assert placement["spacing"]["mode"] == "loose"
    assert placement["gaps"] == ["instance_without_row"]


def test_reconstructed_master_is_restored_but_identity_and_provenance_are_checked():
    _, service, args = selection()
    record = service.bind(args)
    master = deepcopy(record["master"])
    master.update(parameters=master["parameters"][:1], parameters_complete=False)
    bindings = dict(project_ref=record["project_ref"], masters=[master])
    assert service.resolve_bindings(bindings)["masters"] == [record["master"]]
    assert len(bindings["masters"][0]["parameters"]) == 1
    for key in ("id", "target", "library_path", "revision", "kind", "pdk_binding"):
        changed = deepcopy(bindings)
        changed["masters"][0][key] = "tampered"
        with pytest.raises(CircuitSpecError):
            service.resolve_bindings(changed)
    with pytest.raises(CircuitSpecError, match="project binding differs"):
        service.resolve_bindings({**bindings, "project_ref": "another"})
    del master["pdk_binding"]
    with pytest.raises(CircuitSpecError, match="provenance was removed"):
        service.resolve_bindings(bindings)
    with pytest.raises(CircuitSpecError, match="expired"):
        service.resolve_request(selected_request("pdk-adapter:expired"))
    with pytest.raises(CircuitSpecError, match="duplicate"):
        service.resolve_request({**selected_request(record["binding_ref"]),
                                 "binding_refs": [record["binding_ref"]] * 2})


@pytest.mark.parametrize("changed", [
    {"session_ref": "session:another"}, {"bridge_generation": 2}, {"workspace": "/another"},
])
def test_retained_selections_cannot_mix_session_or_project_contexts(changed):
    bridge, service, args = selection(1)
    first = service.bind(args)
    bridge.ctx.update(changed)
    found = service.session.call("search_pdk_devices", {"library": "synthetic"})
    args.update(snapshot_ref=found["context"]["snapshot_ref"], master_id="another")
    second = service.bind(args)
    with pytest.raises(CircuitSpecError, match="span sessions or project contexts"):
        service.resolve_request({**selected_request(first["binding_ref"]),
                                 "binding_refs": [first["binding_ref"], second["binding_ref"]]})


@pytest.mark.parametrize("rows,code", [
    ([], "device_support_missing"),
    ([["a", "v1", False]], "device_support_missing"),
    ([["a", "v1", True], ["b", "v1", True]], "device_support_ambiguous"),
])
def test_missing_invalid_and_ambiguous_support_do_not_request_user_identifiers(rows, code):
    _, service, args = selection()
    service.extension_reader = lambda _: {"items": rows}
    with pytest.raises(DeviceSupportUnavailable) as error:
        service.bind(args)
    assert error.value.code == code
    assert error.value.detail["user_input_required"] is False
    capability = error.value.detail["capability"]
    assert capability["metadata_ready"] is None
    assert capability["copilot_extension_registered"] is (code == "device_support_ambiguous")
    assert capability["automatic_creation_supported"] is None
    assert capability["creator_validated"] is None
    assert capability["builtin_cdf_executor_available"] is False
    assert not service.records


def test_callback_free_device_needs_no_extension_and_wrong_explicit_ref_fails():
    _, service, args = selection(1, False)
    service.extension_reader = lambda _: pytest.fail("unnecessary native query")
    assert service.bind(args)["master"]["callbacks"] == {"status": "none"}
    _, service, args = selection()
    with pytest.raises(DeviceSupportUnavailable, match="Device support"):
        service.bind({**args, "extension_ref": "fabricated"})


@pytest.mark.parametrize("rows", [True, [[1, "v", True]], [["a", "v", "true"]],
                                 [["a", "v", True], ["a", "v", True]]])
def test_native_discovery_evidence_requires_typed_unique_rows(rows):
    class Client:
        def call(self, method, inputs):
            return True, {"value": json.dumps({"ok": True, "items": rows})}

    with pytest.raises(DeviceSupportUnavailable):
        discover_extensions(Client(), dict(target=dict(library="pdk", cell="m", view="symbol"),
                                            library=dict(resolved_path="/pdk")))
