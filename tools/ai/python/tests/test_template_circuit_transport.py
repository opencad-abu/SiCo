"""Public MCP template/spec/geometry handoff, without live session calls."""

import copy
import json

import pytest
from template_circuit_fixture import geometry_args, persisted_case

from cadai.circuit_create import build_create_call, payload
from cadai.circuit_generic import call_generic
from cadai.circuit_spec_schema import CircuitSpecError, digest, validate
from cadai.mcp import TOOLS, McpServer
from cadai.template_circuit import prepare_template


class NoLiveClient:
    def call(self, *args, **kwargs):
        raise AssertionError("template adapter must not access Virtuoso")


def rpc(server, name, args):
    return server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
    )


def unpack(response):
    assert "error" not in response and not response["result"]["isError"]
    return json.loads(response["result"]["content"][0]["text"])


def test_mcp_prepare_preview_geometry_chain_with_workspace_catalog(tmp_path, monkeypatch):
    args, _ = persisted_case(tmp_path, monkeypatch)
    server = McpServer(NoLiveClient(), workspace=tmp_path)
    schema = {t["name"]: t["inputSchema"] for t in TOOLS}
    validate(args, schema["prepare_template_circuit"])
    prepared = unpack(rpc(server, "prepare_template_circuit", args))
    request = {
        "spec": prepared["spec"],
        "bindings": args["bindings"],
        "template_use": prepared["template_use"],
    }
    validate(request, schema["preview_circuit_spec"])
    preview = unpack(rpc(server, "preview_circuit_spec", request))
    assert preview["preview_digest"] == prepared["preview_digest"]
    geometry = geometry_args(prepared)
    validate(geometry, schema["preview_circuit_geometry"])
    output = unpack(rpc(server, "preview_circuit_geometry", geometry))
    plan = output["plan"]
    assert plan["geometry_checks_passed"] and len(plan["wires"]) == 12
    assert output["geometry_plan_digest"] == digest(plan)
    assert plan["preview_digest"] == preview["preview_digest"]
    assert plan["template_use"] == prepared["template_use"]
    assert plan["template_evidence"] == preview["plan"]["template_evidence"]
    assert plan["instances"][1]["xy"] == [3, 23]
    assert plan["template_placement_reference"]["instances"][1]["relative_xy"] == [0, 1000]
    assert not plan["creation_ready"] and not plan["label_rendering_verified"]
    assert plan["geometry_evidence_kind"] == "test_fixture"
    assert not plan["binding_source_verified"] and not plan["live_binding_verified"]


@pytest.mark.parametrize(
    "case",
    ["drop_use", "wrong_digest", "edit_endpoint", "edit_parameters", "null_use", "stale_geometry"],
)
def test_geometry_cannot_bypass_template_or_parameter_checks(tmp_path, monkeypatch, case):
    args, _ = persisted_case(tmp_path, monkeypatch)
    prepared = prepare_template(args, workspace=tmp_path)
    request = geometry_args(prepared)
    if case == "drop_use":
        request.pop("template_use")
    elif case == "wrong_digest":
        request["preview_digest"] = "0" * 64
    elif case == "edit_endpoint":
        request["spec"]["instances"][0]["connections"]["G"] = "Y"
    elif case in {"edit_parameters", "stale_geometry"}:
        request["spec"]["instances"][0]["parameters"]["width"] = "NEW_WIDTH"
        if case == "stale_geometry":
            preview = call_generic(
                "preview_circuit_spec",
                {k: request[k] for k in ("spec", "bindings", "template_use")},
                workspace=tmp_path,
            )
            request["preview_digest"] = preview["preview_digest"]
    else:
        request["template_use"] = None
    with pytest.raises(CircuitSpecError):
        call_generic("preview_circuit_geometry", request, workspace=tmp_path)


def test_mcp_errors_and_verification_profile_remain_bounded(tmp_path, monkeypatch):
    args, _ = persisted_case(tmp_path, monkeypatch)
    server = McpServer(NoLiveClient(), workspace=tmp_path)
    bad = copy.deepcopy(args)
    bad["devices"].pop()
    assert rpc(server, "prepare_template_circuit", bad)["error"]["code"] == -32602
    missing = copy.deepcopy(args)
    missing["template_ref"] = "tpl_" + "0" * 64
    response = rpc(server, "prepare_template_circuit", missing)["result"]
    assert response["isError"]
    assert json.loads(response["content"][0]["text"])["code"] == "template_circuit_unavailable"
    verification = McpServer(NoLiveClient(), workspace=tmp_path, profile="verification")
    assert rpc(verification, "prepare_template_circuit", args)["error"]["code"] == -32602


def test_configured_reference_root_used_in_entire_preview_chain(tmp_path, monkeypatch):
    reference_workspace = tmp_path / "references"
    args, _ = persisted_case(reference_workspace, monkeypatch)
    monkeypatch.setenv(
        "SICO_CIRCUIT_TEMPLATES_DIR", str(reference_workspace / ".cad/ai/reference/circuit-templates")
    )
    workspace = tmp_path / "another_workspace"
    workspace.mkdir()
    server = McpServer(NoLiveClient(), workspace=workspace)
    prepared = unpack(rpc(server, "prepare_template_circuit", args))
    output = unpack(rpc(server, "preview_circuit_geometry", geometry_args(prepared)))
    assert output["plan"]["geometry_checks_passed"]


def test_template_backed_creation_payload_keeps_digest_and_target_parameters(tmp_path, monkeypatch):
    args, _ = persisted_case(tmp_path, monkeypatch)
    prepared = prepare_template(args, workspace=tmp_path)
    request = geometry_args(prepared)
    request["layout"]["grid"] = 0.0625
    geometry = call_generic("preview_circuit_geometry", request, workspace=tmp_path)
    request.update(
        geometry_plan_digest=geometry["geometry_plan_digest"],
        task_ref="task:fixture",
        request_id="fixture-only",
    )
    code = build_create_call("prepare_circuit_creation", request, workspace=tmp_path)
    assert code.startswith("aiCreatePrepare(") and geometry["geometry_plan_digest"] in code
    assert "TARGET_WIDTH_a" in code and "SOURCE_WIDTH" not in code
    assert "gpdk045" not in code and "fixture_a" in code
    data = payload(request, workspace=tmp_path)
    assert data[0] == ["work_a", "adapted_inv", "schematic"]
    assert dict(data[1][0][6]) == {"G": "A", "D": "Y", "S": "gnd!", "B": "gnd!"}
    request["spec"]["instances"][0]["connections"]["G"] = "Y"
    with pytest.raises(CircuitSpecError, match="endpoints differ"):
        build_create_call("prepare_circuit_creation", request, workspace=tmp_path)


def test_mcp_creation_rechecks_template_before_native_dispatch(tmp_path, monkeypatch):
    args, _ = persisted_case(tmp_path, monkeypatch)
    prepared = prepare_template(args, workspace=tmp_path)
    request = geometry_args(prepared)
    request["layout"]["grid"] = 0.0625
    geometry = call_generic("preview_circuit_geometry", request, workspace=tmp_path)
    request.update(
        geometry_plan_digest=geometry["geometry_plan_digest"],
        task_ref="task:fixture",
        request_id="fixture-only",
    )

    class CaptureClient:
        def call(self, name, arguments):
            assert (
                name == "eval_skill_native"
                and geometry["geometry_plan_digest"] in arguments["code"]
            )
            return False, {
                "code": "test_boundary",
                "message": "native execution deliberately replaced by recorder",
            }

    server = McpServer(CaptureClient(), workspace=tmp_path)
    response = rpc(server, "prepare_circuit_creation", request)["result"]
    assert response["isError"]
    assert json.loads(response["content"][0]["text"])["code"] == "test_boundary"
    request["template_use"]["template_ref"] = "tpl_" + "0" * 64
    server = McpServer(NoLiveClient(), workspace=tmp_path)
    response = rpc(server, "prepare_circuit_creation", request)["result"]
    assert response["isError"]
    assert json.loads(response["content"][0]["text"])["code"] == "template_circuit_unavailable"
