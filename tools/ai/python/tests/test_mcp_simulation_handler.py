"""Simulation lifecycle routing contracts after MCP handler extraction."""

import json

import pytest

from cadai import mcp_bindings as mcp_module
from cadai import mcp_simulation_handler as handler
from cadai.mcp import _TOOLS_BY_NAME, McpServer
from cadai.simulation_recipe import SIMULATION_NAMES


class FailingClient:
    def call(self, *args, **kwargs):
        raise OSError("fixture bridge unavailable")


@pytest.mark.parametrize("name", sorted(SIMULATION_NAMES))
def test_simulation_argument_and_transport_errors_are_handler_errors(name):
    with pytest.raises(handler.SimulationHandlerArgumentError):
        handler.dispatch_simulation(name, {}, client=FailingClient())


def test_unknown_simulation_name_is_rejected():
    with pytest.raises(ValueError, match="unknown simulation tool"):
        handler.dispatch_simulation("unknown_simulation_tool", {}, client=None)


def test_simulation_names_are_registered_and_verification_filters_them():
    assert SIMULATION_NAMES <= _TOOLS_BY_NAME.keys()
    server = McpServer(None, profile="verification")
    for name in SIMULATION_NAMES:
        response = server.handle({
            "id": name,
            "method": "tools/call",
            "params": {"name": name, "arguments": {}},
        })
        assert response["error"]["code"] == -32602


def test_server_routes_simulation_handler_and_preserves_invalid_params(monkeypatch):
    from simulation_recipe_fixture import recipe

    seen = []

    def fake_dispatch(name, arguments, *, client):
        seen.append((name, arguments, client))
        raise handler.SimulationHandlerArgumentError("fixture invalid")

    monkeypatch.setattr(mcp_module, "dispatch_simulation", fake_dispatch)
    client = object()
    arguments = {"recipe": recipe()}
    response = McpServer(client).handle({
        "id": "simulation-1",
        "method": "tools/call",
        "params": {"name": "preview_simulation_recipe", "arguments": arguments},
    })
    assert response["error"]["code"] == -32602
    assert seen == [("preview_simulation_recipe", arguments, client)]


def test_server_preserves_simulation_tool_failure_envelope(monkeypatch):
    monkeypatch.setattr(mcp_module, "dispatch_simulation", lambda *args, **kwargs: (
        False, {"code": "fixture_unavailable", "message": "fixture"}
    ))
    response = McpServer(None).handle({
        "id": 4,
        "method": "tools/call",
        "params": {"name": "get_simulation_recipe_status", "arguments": {"run_ref": "run:1"}},
    })
    assert response["result"]["isError"]
    assert json.loads(response["result"]["content"][0]["text"])["code"] == "fixture_unavailable"
