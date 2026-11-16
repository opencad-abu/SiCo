"""Structured CDF writes, native ordering, and honest capability diagnostics."""

import json
from copy import deepcopy

import pytest
from pdk_binding_fixture import fixture, tagged

from cadai.cdf_tools import build_call
from cadai.cdf_update import call_cdf_update
from cadai.circuit_spec_schema import CircuitSpecError
from cadai.pdk_binding_data import parameter_rows, read_device
from cadai.pdk_extensions import DeviceSupportUnavailable
from sico.transport.circuit import literal_call


class Client:
    def __init__(self):
        self.calls = []

    def call(self, method, arguments):
        self.calls.append((method, arguments))
        return True, {"value": json.dumps({"ok": True})}


def request():
    return dict(cdf_ref="cdf:" + "a" * 32, task_ref="task:test", request_id="edit",
                parameters={"w": "2u", "fingers": "4", "enabled": False, "scale": 1.5},
                callback_order=["fingers", "w", "enabled", "scale"])


def test_prepare_compiles_typed_data_in_explicit_order():
    client, args = Client(), request()
    before = deepcopy(args)
    assert call_cdf_update("prepare_cdf_update", args, client)[0]
    method, inputs = client.calls[0]
    assert method == "eval_skill_native"
    function, values = literal_call(inputs["code"])
    assert function == "aiCdfUpdatePrepare"
    assert values[-1] == [["fingers", "string", "4"], ["w", "string", "2u"],
                          ["enabled", "boolean", None], ["scale", "number", 1.5]]
    assert args == before


@pytest.mark.parametrize("order", [[], ["w"], ["w", "w", "enabled", "scale"],
                                   ["w", "fingers", "enabled", "scale", "unknown"]])
def test_incomplete_duplicate_or_extra_order_never_dispatches(order):
    client = Client()
    with pytest.raises(CircuitSpecError):
        call_cdf_update("prepare_cdf_update", dict(request(), callback_order=order), client)
    assert not client.calls


def test_probe_uses_builtin_without_registration():
    args = request()
    args["target"] = dict(library="SicoTest", cell="probe", view="schematic")
    code = build_call("probe_cdf_parameters", args)
    assert '"builtin:cdf-callbacks:v1"' in code
    assert code.index('list("fingers"') < code.index('list("w"')


@pytest.mark.parametrize("condition", [None, "nil", "t", "cdfGuiOnly()"])
def test_builtin_ignores_gui_editable_and_supports_cyclic(condition):
    _, service, args = fixture()
    device, _ = read_device(service.session, args)
    from cadai.pdk_normalize import condition as normalize
    param = device["parameters"]["items"][0]
    param.update(cdf_type="cyclic", choices=tagged(["Width", "Length"]),
                 editable=normalize(tagged(condition)))
    args.update(extension_ref="builtin:cdf-callbacks:v1", callback_order=["w"])
    args["writable_parameters"][0]["policy"] = "cdf_callback"
    rows, callbacks, _ = parameter_rows(device, args)
    assert rows[0] == dict(name="w", type="string", editable=True, choices=["Width", "Length"])
    assert callbacks["order"] == ["w"]


@pytest.mark.parametrize("diagnostic", [{}, {"items": None}, "unavailable",
    {"items": [["builtin:cdf-callbacks:v1", "v1", True]]}])
def test_discovery_does_not_claim_request_qualification(diagnostic):
    error = DeviceSupportUnavailable("device_support_missing",
        dict(library="pdk", cell="mos", view="symbol"), diagnostic)
    capability = error.detail["capability"]
    assert capability["metadata_ready"] is None
    assert capability["creator_validated"] is None
    assert capability["automatic_creation_supported"] is None
    assert capability["copilot_extension_registered"] is False
    assert error.detail["user_input_required"] is False


def test_existing_cdf_write_mcp_does_not_require_pdk_reselection(monkeypatch, tmp_path):
    from cadai import mcp
    server = mcp.McpServer(Client(), workspace=tmp_path)
    server.pdk_selection_guard = lambda _: pytest.fail("existing instance requires no PDK selection")
    from cadai import mcp_project_handler
    monkeypatch.setattr(mcp_project_handler, "call_project", lambda *args: {"ok": True})
    response = server.handle(dict(jsonrpc="2.0", id="update", method="tools/call",
        params=dict(name="execute_circuit_operation", arguments=dict(operation="apply_cdf_update",
            arguments=dict(prepare_ref="cdf-update-prepare:test", request_id="test")))))
    assert not response["result"]["isError"]


def test_direct_cdf_apply_mcp_refuses_native_dispatch(tmp_path):
    from cadai.mcp import McpServer
    client = Client()
    server = McpServer(client, workspace=tmp_path)
    response = server.handle(dict(jsonrpc="2.0", id="update", method="tools/call",
        params=dict(name="apply_cdf_update", arguments=dict(prepare_ref="cdf-update-prepare:test",
                                                            request_id="test"))))
    assert "execute_circuit_operation" in response["error"]["message"]
    assert not client.calls
