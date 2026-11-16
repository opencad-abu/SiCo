"""Optional data never gates an agent; configuration survives until tool use."""

import json
import os
from pathlib import Path

import pytest

from cadai.launch import build_claude_mcp_config, build_codex_command, prepare_terminal_environment
from cadai.mcp import McpServer
from cadai.optional_resources import PATH_NAMES, prepare
from cadai.runtime import RuntimePaths
from cadai.template_locations import normalize_root, template_locations
from cadai.template_schema import TemplateUnavailable
from cadai.template_catalog import call_template


@pytest.mark.parametrize("agent", ["codex", "claude"])
@pytest.mark.parametrize("value", ["", "data/missing", "/missing/data", "/bad\npath"])
def test_optional_paths_do_not_block_either_terminal(tmp_path, monkeypatch, agent, value):
    for name in list(os.environ):
        if name.startswith(("SICO_", "CAD_")):
            monkeypatch.delenv(name)
    for name in PATH_NAMES:
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SICO_TEMP_DIR", ".sico")
    monkeypatch.setenv("CAD_TEMP_DIR", "")
    runtime = RuntimePaths(tmp_path, tmp_path / "socket", tmp_path / "spool")
    environment, warnings = prepare_terminal_environment(runtime, "token", tmp_path, None, agent)
    expected = str(tmp_path / value) if value == "data/missing" else value
    for name in PATH_NAMES:
        assert environment[name] == expected
    assert bool(warnings) == (value == "/bad\npath")
    assert environment["SICO_TEMP_DIR"] == str(tmp_path / ".sico")
    assert not (tmp_path / "data").exists()
    if agent == "codex":
        command = build_codex_command("/bin/codex", "/bin/python3", tmp_path,
                                      tmp_path, 30, environment=environment)
        names = next(row.split("=", 1)[1] for row in command
                     if row.startswith("mcp_servers.sico_ic.env_vars="))
        assert set(PATH_NAMES) <= set(json.loads(names))
        assert "SICO_API_KEY" not in json.loads(names)
    else:
        config = json.loads(build_claude_mcp_config(
            "/bin/python3", tmp_path, runtime, "token", 30, environment=environment))
        assert all(config["mcpServers"]["sico_ic"]["env"][name] == expected for name in PATH_NAMES)


def test_optional_legacy_values_are_diagnosed_only_at_tool_use(tmp_path):
    env = {"CAD_AI_TEMPLATE_ROOT": "old/path"}
    assert "was removed" in prepare(env, tmp_path)[0]
    assert env == {"CAD_AI_TEMPLATE_ROOT": "old/path"}
    with pytest.raises(ValueError, match="removed"):
        template_locations(tmp_path, env)


def test_relative_template_is_bound_to_workspace_after_chdir(tmp_path, monkeypatch):
    workspace = tmp_path / "work"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)
    assert normalize_root("data/templates", workspace=workspace) == workspace / "data/templates"
    locations = template_locations(workspace, {"SICO_CIRCUIT_TEMPLATES_DIR": "data/templates"})
    assert next(row.path for row in locations if row.tier == "project") == workspace / "data/templates"


def test_mcp_initialize_and_tools_list_do_not_read_optional_data(tmp_path, monkeypatch):
    for name in PATH_NAMES:
        monkeypatch.setenv(name, "/bad\npath")
    class Client:
        def call(self, *args, **kwargs):
            pytest.fail("MCP startup must not inspect live PDK data")

        def close(self):
            pass

    server = McpServer(Client(), workspace=tmp_path)
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed["result"]["tools"]
    assert server._pdk_session is None
    reference = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "search_skill_api", "arguments": {"query": "dbOpen"}}})
    assert reference["result"]["isError"]
    assert "control characters" in str(reference)
    server.skill_reference.close()
    with pytest.raises(TemplateUnavailable, match="control characters"):
        call_template("query_circuit_templates", {}, workspace=tmp_path)
