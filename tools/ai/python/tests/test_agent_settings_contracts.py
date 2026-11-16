"""AS3 credential, discovery identity, preview and measurement boundaries."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sico.codex.external_mcp import ExternalMcp
from sico.codex.resource_metrics import summaries
from sico.codex.resource_probe import CheckError, call_tool
from sico.codex.resource_skills import dependencies, preview
from test_agent_settings_native import server
from test_agent_settings_probe import probe_settings


def test_optional_missing_environment_is_disabled_and_required_fails(tmp_path):
    row = server(tmp_path)
    row["required"] = False
    settings = probe_settings()
    del settings.environment["EXTERNAL_TOKEN"]
    projection = ExternalMcp({"mcp_servers": [row]}, settings)
    assert projection.config["external"]["enabled"] is False
    assert projection.statuses([])[0]["status"] == "auth_required"
    row["required"] = True
    with pytest.raises(ValueError, match="必需 MCP 缺少环境引用"):
        ExternalMcp({"mcp_servers": [row]}, settings)
    row["enabled"] = False
    assert ExternalMcp({"mcp_servers": [row]}, settings).statuses([])[0]["status"] == "disabled"


@pytest.mark.parametrize("reference", ["SICO_API_KEY", "EXTERNAL_TOKEN", "CUSTOM_MODEL_KEY"])
def test_model_key_cannot_be_referenced_even_through_an_alias(tmp_path, reference):
    row = server(tmp_path)
    row["env_refs"] = {"TOKEN": reference}
    settings = probe_settings()
    settings.options["api_key_env"] = "CUSTOM_MODEL_KEY"
    settings.environment.update(CUSTOM_MODEL_KEY="model-secret", EXTERNAL_TOKEN="model-secret")
    with pytest.raises(ValueError, match="不允许引用模型"):
        ExternalMcp({"mcp_servers": [row]}, settings)


def test_effective_native_config_cannot_expand_or_replace_the_projection(tmp_path):
    external = ExternalMcp({"mcp_servers": [server(tmp_path)]}, probe_settings())
    config = dict(external.config)
    rpc = SimpleNamespace(request=lambda *_a, **_k: {"config": {"mcp_servers": config}})
    external.verify(rpc, str(tmp_path))
    config["unselected"] = {"command": "arbitrary"}
    with pytest.raises(ValueError, match="清单之外"):
        external.verify(rpc, str(tmp_path))
    del config["unselected"]
    config["external"] = {**config["external"], "env": {"SECRET": "bad"}}
    with pytest.raises(ValueError, match="配置覆盖"):
        external.verify(rpc, str(tmp_path))


def test_explicit_calls_reject_unknown_servers_and_bad_json_before_execution(tmp_path):
    external = ExternalMcp({"mcp_servers": [server(tmp_path)]}, probe_settings())
    requests = []
    rpc = SimpleNamespace(request=lambda *a, **k: requests.append(a))
    native = [{"name": "external", "tools": {"proof": {}}},
              {"name": "sico_ic", "tools": {"eval": {}}}]
    for call in ({"server": "sico_ic", "tool": "eval", "arguments": {}},
                 {"server": "external", "tool": "hidden", "arguments": {}},
                 {"server": "external", "tool": "proof", "arguments": '{"a":1,"a":2}'}):
        with pytest.raises(CheckError):
            call_tool(rpc, "thread", native, call, external)
    assert requests == []
    rpc.request = lambda *_a, **_k: {"content": [{"text": "external-secret-sentinel"}]}
    assert "external-secret-sentinel" not in call_tool(rpc, "thread", native,
        {"server": "external", "tool": "proof", "arguments": "{}"}, external)


def test_preview_reads_only_discovered_paths_and_bounds_files(tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text("approved body")
    rows = [{"name": "same", "path": str(skill), "definition_path": str(skill)}]
    assert preview(rows, {"name": "same", "path": str(skill)}) == "approved body"
    with pytest.raises(ValueError, match="发现目录"):
        preview(rows, {"name": "same", "path": "/etc/passwd"})
    skill.write_text("x" * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        preview(rows, {"name": "same", "path": str(skill)})


def test_dependencies_use_forwarded_names_and_mcp_connection_status():
    skills = [dict(name="design", path="/skills/SKILL.md", dependencies=[
        {"type": "env_var", "value": "SICO_API_KEY"}, {"type": "env_var", "value": "PATH"},
        {"type": "mcp", "value": "good"}, {"type": "mcp", "value": "absent"}])]
    servers = [dict(name="good", runtimeStatus="connected", toolsError=None)]
    assert [d["status"] for d in dependencies(skills, servers, {"PATH"})] == [
        "missing", "available", "available", "missing"]


def test_runtime_measurement_matches_m4_and_preserves_stable_observations():
    utility = str(Path(__file__).resolve().parents[3] / "utility")
    sys.path.insert(0, utility)
    try:
        from mcp_catalog_measure import measurement

        row = {"name": "read", "description": "中文", "inputSchema": {"type": "object"},
               "annotations": None}
        servers = [{"name": "remote", "tools": {"read": row}}]
        first = summaries(servers)
        measured = measurement([row])
        assert first[0]["array_compact_bytes"] == measured["array_compact_bytes"]
        assert first[0]["sha256"] == measured["sha256"]
        assert summaries(servers, first) == first
        row["description"] = "changed"
        changed = summaries(servers, first)
        assert changed[0]["changed"] and changed[0]["prior_sha256"] == first[0]["sha256"]
        assert "中文" not in json.dumps(first)
    finally:
        sys.path.remove(utility)
