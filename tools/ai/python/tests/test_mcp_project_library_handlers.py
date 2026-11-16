"""Project/library MCP handler routing contracts."""

import json

import pytest

from cadai import mcp_bindings as mcp_module
from cadai import mcp_library_handler as library_handler
from cadai import mcp_project_handler as project_handler
from cadai.circuit_library import LIBRARY_NAMES
from cadai.mcp import _TOOLS_BY_NAME, McpServer
from cadai.project_schema import PROJECT_NAMES


def test_project_and_library_names_are_registered():
    assert PROJECT_NAMES | LIBRARY_NAMES <= _TOOLS_BY_NAME.keys()


def test_project_handler_rejects_unknown_and_maps_contract_errors():
    with pytest.raises(ValueError, match="unknown project tool"):
        project_handler.dispatch_project("unknown_project_tool", {}, client=None,
                                         workspace=None, definitions={})

    class Invalid:
        def call_project(self, *args):
            raise AssertionError

    with pytest.raises(project_handler.ProjectHandlerArgumentError):
        project_handler.dispatch_project("preflight_circuit_project", {}, client=Invalid(),
                                         workspace=None, definitions={})


def test_library_handler_rejects_direct_create():
    with pytest.raises(
        library_handler.LibraryHandlerArgumentError,
        match="execute_circuit_operation",
    ):
        library_handler.dispatch_library("create_circuit_library", {}, client=None, workspace=None)


def test_server_routes_project_handler(monkeypatch, tmp_path):
    seen = []

    def fake(name, arguments, *, client, workspace, definitions, pdk_bindings=None):
        seen.append((name, arguments, client, workspace, definitions, pdk_bindings))
        raise project_handler.ProjectHandlerArgumentError("fixture invalid")

    monkeypatch.setattr(mcp_module, "dispatch_project", fake)
    client = object()
    server = McpServer(client, workspace=tmp_path)
    server._pdk_session = object()
    server._pdk_bindings = object()
    response = server.handle({
        "id": 1, "method": "tools/call",
        "params": {"name": "preflight_circuit_project", "arguments": {
            "presentation": "background",
            "targets": [{
                "library": "Work", "cell": "Cell", "view": "schematic",
                "intent": "read", "oa_view_type": "schematic",
            }],
            "libraries": [],
            "directories": [{
                "path": "/tmp", "purpose": "project",
                "min_free_bytes": 0, "min_free_inodes": 0,
            }],
            "model_files": [],
        }},
    })
    assert response["error"]["code"] == -32602
    assert seen and seen[0][0] == "preflight_circuit_project"


def test_server_preserves_library_failure_envelope(monkeypatch):
    monkeypatch.setattr(mcp_module, "dispatch_library", lambda *args, **kwargs: (
        False, {"code": "fixture_unavailable", "message": "fixture"}
    ))
    response = McpServer(None).handle({
        "id": 2, "method": "tools/call",
        "params": {"name": "prepare_library", "arguments": {"request_id": "fixture"}},
    })
    assert response["result"]["isError"]
    assert json.loads(response["result"]["content"][0]["text"])["code"] == "fixture_unavailable"
