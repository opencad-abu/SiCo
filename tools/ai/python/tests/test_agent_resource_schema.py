"""Versioned manifests share strict validation before save or runtime loading."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sico.agent_resources import store
from sico.agent_resources.manifest import read_selection
from sico.agent_resources.schema import KINDS, normalize_manifest, upgrade_manifest
from sico.agent_resources.snapshot import difference
from sico.codex.resource_config import ResourceConfig
from test_codex_resources_runtime import make_resources


def extensions():
    return {
        "version": 2,
        "mcp_servers": [{"id": "docs", "name": "Documentation", "source": "project",
                         "enabled": True, "transport": "stdio", "command": "docs-server",
                         "args": ["--scope", "local", "--scope", "project"], "cwd": "/tmp",
                         "env_refs": {"TOKEN": "DOCS_TOKEN"}, "required": False,
                         "startup_timeout_sec": 10, "enabled_tools": ["search", "read"]}],
        "knowledge_sources": [
            {"id": "local", "name": "工艺说明", "source": "project", "enabled": True,
             "type": "local", "path": "/tmp/docs", "formats": ["markdown", "pdf"],
             "include": ["**/*"], "exclude": ["private/**"], "update": "manual"},
            {"id": "remote", "name": "远程资料", "source": "user", "enabled": False,
             "type": "remote", "adapter": "search", "url": "https://example.org/search",
             "credential_ref": "SEARCH_TOKEN", "collection": "process", "timeout_sec": 30}],
        "skill_overrides": [{"path": "/tmp/docs/SKILL.md", "enabled": False}],
    }


def test_upgrade_retains_v1_and_is_an_explicit_pure_projection(tmp_path):
    manifest, original = make_resources(tmp_path)
    raw = manifest.read_bytes()
    copy = deepcopy(original)
    upgraded = upgrade_manifest(original)
    assert original == copy and manifest.read_bytes() == raw
    for kind in KINDS:
        assert upgraded[kind] == [{**r, "enabled": True} for r in original[kind]]
    assert upgrade_manifest(upgraded) == upgraded
    assert difference(original, upgraded) == []
    upgraded["plugins"].clear()
    assert difference(original, upgraded) == [
        {"kind": "plugins", "id": "plugin", "change": "removed"}]


def test_v2_enablement_and_lexical_paths_are_preserved(tmp_path):
    manifest, original = make_resources(tmp_path)
    linked = tmp_path / "linked"
    linked.symlink_to(tmp_path / "skills", target_is_directory=True)
    value = upgrade_manifest(original)
    value["skill_roots"][0]["path"] = str(linked)
    value["plugins"][0].update(enabled=False, path=str(tmp_path / "missing"))
    manifest.write_text(json.dumps(value))
    provider = SimpleNamespace(options={"external_resources": str(manifest)})
    runtime = ResourceConfig(read_selection(provider, tmp_path))
    assert runtime.ready("skill_roots")[0]["path"] == str(linked)
    assert runtime.rows[1]["status"] == "disabled"
    assert not runtime.ready("plugins")
    assert normalize_manifest(value)["skill_roots"][0]["path"] == str(linked)


@pytest.mark.parametrize("kind", ["knowledge_sources"])
def test_future_types_validate_but_cannot_be_silently_saved_or_loaded(tmp_path, kind):
    value = {"version": 2, kind: extensions()[kind]}
    assert normalize_manifest(value)[kind] == value[kind]
    with pytest.raises(ValueError, match="尚未接通"):
        store.save_project(tmp_path, value, expected_revision=None)
    assert not (tmp_path / ".sico").exists()
    selected = {"path": str(tmp_path / "resources.json"), "source": "project",
                "revision": "a" * 64, "manifest": value}
    with pytest.raises(ValueError, match="尚未接通"):
        ResourceConfig(selected)


@pytest.mark.parametrize("kind,field,value", [
    ("mcp_servers", "enabled", 1), ("mcp_servers", "required", "true"),
    ("mcp_servers", "startup_timeout_sec", True), ("mcp_servers", "startup_timeout_sec", 0),
    ("mcp_servers", "startup_timeout_sec", float("nan")),
    ("mcp_servers", "startup_timeout_sec", 601), ("mcp_servers", "id", "sico_ic"),
    ("mcp_servers", "id", "sico_ae"), ("mcp_servers", "id", "sico_cc"),
    ("mcp_servers", "id", "sico_knowledge"), ("mcp_servers", "cwd", "relative"),
    ("mcp_servers", "env_refs", {"TOKEN": "plain secret"}),
    ("mcp_servers", "transport", "sse"), ("mcp_servers", "enabled_tools", ["read", "read"]),
    ("mcp_servers", "unknown", True), ("knowledge_sources", "update", "automatic"),
    ("knowledge_sources", "formats", ["docx"]), ("knowledge_sources", "formats", []),
    ("knowledge_sources", "include", "*.md"), ("skill_overrides", "path", "/tmp/readme.md"),
    ("skill_overrides", "enabled", None),
])
def test_extension_types_and_values_are_strict(kind, field, value):
    config = extensions()
    config[kind][0][field] = value
    with pytest.raises(ValueError):
        normalize_manifest(config)


@pytest.mark.parametrize("url", ["file:///tmp/x", "https://u:p@example.org", "https://x/?token=secret",
                                 "https://x/#secret", "https://x:bad", "https://"])
def test_credentials_cannot_be_hidden_in_http_urls(url):
    config = {"version": 2, "mcp_servers": [{"id": "docs", "name": "Docs", "source": "user",
              "enabled": True, "transport": "streamable_http", "url": url}]}
    with pytest.raises(ValueError):
        normalize_manifest(config)


def test_http_and_cross_type_duplicate_ids():
    config = extensions()
    config["mcp_servers"][0] = {"id": "docs", "name": "Docs", "source": "user", "enabled": True,
                               "transport": "streamable_http", "url": "https://x/mcp",
                               "credential_ref": "DOCS_TOKEN", "tool_timeout_sec": 20}
    assert normalize_manifest(config)["mcp_servers"] == config["mcp_servers"]
    config["knowledge_sources"][0]["id"] = "docs"
    with pytest.raises(ValueError, match="重复"):
        normalize_manifest(config)


def test_enabled_is_required_for_v2_and_duplicate_skill_paths_fail(tmp_path):
    _, value = make_resources(tmp_path)
    value["version"] = 2
    with pytest.raises(ValueError):
        normalize_manifest(value)
    config = extensions()
    config["skill_overrides"] *= 2
    with pytest.raises(ValueError, match="重复"):
        normalize_manifest(config)


def test_duplicate_plugin_identity_stops_loading(tmp_path):
    path, value = make_resources(tmp_path)
    value["plugins"].append({**value["plugins"][0], "id": "duplicate"})
    path.write_text(json.dumps(value))
    provider = SimpleNamespace(options={"external_resources": str(path)})
    with pytest.raises(ValueError, match="身份"):
        ResourceConfig(read_selection(provider, tmp_path))
