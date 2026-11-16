"""CDF routing contracts after MCP handler extraction."""

import json

import pytest

import cadai.mcp_bindings as mcp_module
from cadai import mcp_cdf_handler as handler
from cadai.mcp import McpServer, _TOOLS_BY_NAME
from cadai.cdf_tools import CDF_NAMES
from cadai.cdf_update import CDF_UPDATE_NAMES
from cadai.circuit_spec_schema import CircuitSpecError


@pytest.mark.parametrize("name", sorted(CDF_NAMES | CDF_UPDATE_NAMES))
def test_cdf_argument_errors_become_mcp_invalid_params(monkeypatch, name):
    def invalid(*args, **kwargs):
        raise CircuitSpecError("fixture invalid")

    if name in CDF_NAMES:
        monkeypatch.setattr(handler, "call_cdf", invalid)
    else:
        monkeypatch.setattr(handler, "call_cdf_update", invalid)
    response = McpServer(None).handle({
        "id": name,
        "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    })
    assert response["error"]["code"] == -32602


def test_direct_cdf_apply_keeps_control_plane_error():
    with pytest.raises(handler.CdfHandlerArgumentError, match="execute_circuit_operation"):
        handler.dispatch_cdf(
            "apply_cdf_update",
            {"prepare_ref": "cdf-update-prepare:test", "request_id": "test"},
            client=None,
            workspace=None,
        )


def test_cdf_names_are_registered_and_verification_filters_them():
    names = CDF_NAMES | CDF_UPDATE_NAMES
    assert names <= _TOOLS_BY_NAME.keys()
    server = McpServer(None, profile="verification")
    for name in names:
        response = server.handle({
            "id": name,
            "method": "tools/call",
            "params": {"name": name, "arguments": {}},
        })
        assert response["error"]["code"] == -32602


def test_mcp_server_uses_cdf_handler_result(monkeypatch):
    monkeypatch.setattr(mcp_module, "dispatch_cdf", lambda *args, **kwargs: (
        False, {"ok": False, "code": "cdf_unavailable", "message": "fixture"}
    ))
    response = McpServer(None).handle({
        "id": 1,
        "method": "tools/call",
        "params": {"name": "inspect_cdf", "arguments": {}},
    })
    assert response["result"]["isError"]
    assert json.loads(response["result"]["content"][0]["text"])["code"] == "cdf_unavailable"
