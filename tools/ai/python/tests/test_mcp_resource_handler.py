"""Generic resource MCP handler routing contracts."""

import json

import pytest

from cadai import mcp_bindings as mcp_module
from cadai import mcp_resource_handler as handler
from cadai.mcp import McpServer, _TOOLS_BY_NAME
from cadai.skill_reference import SkillReference


def test_resource_names_are_registered():
    assert handler.RESOURCE_NAMES <= _TOOLS_BY_NAME.keys()


def test_unknown_resource_is_rejected():
    with pytest.raises(ValueError, match="unknown resource tool"):
        handler.dispatch_resource("unknown_resource", {}, workspace=None, skill_reference=SkillReference())


def test_server_routes_resource_argument_error(monkeypatch):
    monkeypatch.setattr(mcp_module, "dispatch_resource", lambda *args, **kwargs: (
        (_ for _ in ()).throw(handler.ResourceHandlerArgumentError("fixture invalid"))
    ))
    response = McpServer(None).handle({
        "id": 1, "method": "tools/call",
        "params": {"name": "Search", "arguments": {}},
    })
    assert response["error"]["code"] == -32602


def test_resource_failure_preserves_tool_envelope(monkeypatch):
    monkeypatch.setattr(mcp_module, "dispatch_resource", lambda *args, **kwargs: (
        False, {"code": "fixture_unavailable", "message": "fixture"}
    ))
    response = McpServer(None).handle({
        "id": 2, "method": "tools/call",
        "params": {"name": "cds_help", "arguments": {}},
    })
    assert response["result"]["isError"]
    assert json.loads(response["result"]["content"][0]["text"])["code"] == "fixture_unavailable"


def test_missing_reference_is_a_structured_optional_capability_error(tmp_path):
    ok, detail = handler.dispatch_resource(
        "search_skill_api", {"query": "substring"},
        workspace=None, skill_reference=SkillReference(tmp_path / "missing-reference"),
    )
    assert ok is False
    assert detail["code"] == "skill_reference_unavailable"
    assert "SICO_AI_SKILL_REFERENCE_ROOT" in detail["message"]
    assert "cds_help" in detail["message"]


def test_help_tools_are_resource_tools():
    assert {"cds_help", "mcp_help"} <= handler.RESOURCE_NAMES
    ok, detail = handler.dispatch_resource(
        "mcp_help", {"action": "describe", "tool": "cds_help"},
        workspace=None, skill_reference=SkillReference())
    assert ok and detail["tool"]["name"] == "cds_help"
    with pytest.raises(handler.ResourceHandlerArgumentError):
        handler.dispatch_resource("mcp_help", {"action": "bogus"},
                                  workspace=None, skill_reference=SkillReference())
