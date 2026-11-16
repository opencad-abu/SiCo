"""Creation and edit MCP handler routing contracts."""

import json

import pytest

from cadai import mcp_bindings as mcp_module
from cadai import mcp_creation_handler as creation_handler
from cadai import mcp_edit_handler as edit_handler
from cadai.circuit_create import CREATE_NAMES
from cadai.circuit_edit import EDIT_NAMES
from cadai.mcp import _TOOLS_BY_NAME, McpServer


def test_creation_and_edit_names_are_registered():
    assert CREATE_NAMES | EDIT_NAMES <= _TOOLS_BY_NAME.keys()


def test_creation_and_edit_unknown_names_are_rejected():
    with pytest.raises(ValueError, match="unknown creation tool"):
        creation_handler.dispatch_creation("unknown_creation_tool", {}, client=None, workspace=None)
    with pytest.raises(ValueError, match="unknown edit tool"):
        edit_handler.dispatch_edit("unknown_edit_tool", {}, client=None, workspace=None)


def test_server_routes_creation_handler(monkeypatch):
    monkeypatch.setattr(mcp_module, "dispatch_creation", lambda *args, **kwargs: (
        (_ for _ in ()).throw(creation_handler.CreationHandlerArgumentError("fixture invalid"))
    ))
    response = McpServer(object()).handle({
        "id": 1, "method": "tools/call",
        "params": {"name": "inspect_circuit_target", "arguments": {}},
    })
    assert response["error"]["code"] == -32602


def test_server_preserves_edit_failure_envelope(monkeypatch):
    monkeypatch.setattr(mcp_module, "dispatch_edit", lambda *args, **kwargs: (
        False, {"code": "fixture_unavailable", "message": "fixture"}
    ))
    response = McpServer(None).handle({
        "id": 2, "method": "tools/call",
        "params": {"name": "execute_circuit_edit", "arguments": {
            "prepare_ref": "prepare:fixture",
            "geometry_plan_digest": "a" * 64,
            "request_id": "fixture",
            "approval_digest": "b" * 64,
        }},
    })
    assert response["result"]["isError"]
    assert json.loads(response["result"]["content"][0]["text"])["code"] == "fixture_unavailable"
