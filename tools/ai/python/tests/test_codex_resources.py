"""Configuration and lifecycle boundaries for explicitly selected resources."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sico.agent_resources.manifest import read_selection
from sico.agent_resources.schema import validate_manifest
from sico.codex.backend import CodexBackend
from sico.codex.resource_config import ResourceConfig
from sico.codex.rpc import RpcError
from sico.core.tools import ToolRegistry
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal
from test_agent_codex_native import CONTEXT
from test_codex_resources_runtime import make_resources


@pytest.mark.parametrize("invalid", [
    {"version": True}, {"version": 3}, {"version": 1, "extra": []},
    {"version": 1, "skill_roots": "bad"},
    {"version": 1, "skill_roots": [{"id": "bad"}]},
    {"version": 1, "skill_roots": [{"id": "x", "path": "relative",
                                    "source": "user", "access": "read"}]},
    {"version": 1, "skill_roots": [{"id": "x", "path": "/tmp",
                                    "source": "user", "access": "execute"}]},
])
def test_reject_invalid_resource_manifest(invalid):
    with pytest.raises(ValueError):
        validate_manifest(invalid)


def test_precedence_missing_paths_and_duplicate_ids(tmp_path):
    manifest, config = make_resources(tmp_path)
    project = tmp_path / "project"
    default = project / ".cad/ai/agent-resources.json"
    default.parent.mkdir(parents=True)
    default.write_text('{"version":1}')
    provider = SimpleNamespace(options={}, environment={})
    assert ResourceConfig(read_selection(provider, project)).rows == []
    provider.environment["CAD_AGENT_EXTERNAL_RESOURCES"] = str(manifest)
    with pytest.raises(ValueError, match="set SICO_EXTERNAL_RESOURCES"):
        ResourceConfig(read_selection(provider, project))
    provider.environment["SICO_EXTERNAL_RESOURCES"] = str(default)
    assert ResourceConfig(read_selection(provider, project)).rows == []
    provider.environment["SICO_EXTERNAL_RESOURCES"] = str(manifest)
    provider.options["external_resources"] = str(default)
    assert ResourceConfig(read_selection(provider, project)).rows == []
    config["skill_roots"][0]["path"] = str(tmp_path / "missing")
    manifest.write_text(json.dumps(config))
    provider.options["external_resources"] = str(manifest)
    selected = ResourceConfig(read_selection(provider, project))
    assert selected.rows[0]["status"] == "unavailable"
    assert not selected.ready("skill_roots")
    config["plugins"][0]["id"] = "user"
    with pytest.raises(ValueError, match="重复"):
        validate_manifest(config)


def test_remote_plugin_sources_and_wrong_identity_are_unavailable(tmp_path):
    manifest, config = make_resources(tmp_path)
    provider = SimpleNamespace(options={"external_resources": str(manifest)}, environment={})
    path = Path(config["plugins"][0]["path"])
    market = json.loads(path.read_text())
    market["plugins"][0]["source"] = {"source": "git", "url": "https://example.org/plugin"}
    path.write_text(json.dumps(market))
    resources = ResourceConfig(read_selection(provider, tmp_path))
    assert not resources.ready("plugins")
    assert resources.rows[1]["load_error"]


def test_bound_roots_keep_selection_when_defaults_change(tmp_path):
    manifest, config = make_resources(tmp_path)
    provider = SimpleNamespace(options={"external_resources": str(manifest)}, environment={})
    with SessionJournal(tmp_path, "resource-binding") as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        backend.resources.prepare()
        roots = backend.resources.config.selected_roots()
        backend._bind_thread("start", {"id": "native-thread"}, expected=None, roots=roots)
    config["capability_roots"] = []
    manifest.write_text(json.dumps(config))
    with SessionJournal(tmp_path, "resource-binding") as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        backend.resources.prepare()
        assert backend.resources.config.selected_roots() == roots
        assert backend.resources.published["configuration"]["pending"]
        assert backend.runtime is None


def test_busy_status_uses_published_snapshot_without_rpc_or_model(tmp_path):
    with SessionJournal(tmp_path, "busy-resource") as journal:
        provider = SimpleNamespace(options={}, environment={})
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        controller = SessionController(backend)
        try:
            recorded = journal.events()
            controller._running = True
            result = controller.backend_commands.resource_status()
            assert result["busy"] and result["status"] == "not_started"
            result["rows"].append({})
            assert backend.resources.published["rows"] == []
            assert backend.runtime is None and journal.events() == recorded
        finally:
            controller._running = False
            controller.close()


def test_missing_resources_disable_managed_plugins_without_copy_or_install(tmp_path):
    manifest, config = make_resources(tmp_path)
    provider = SimpleNamespace(options={"external_resources": str(manifest)}, environment={})
    with SessionJournal(tmp_path, "resource-disable") as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        backend.resources.prepare()
        backend.resources.managed.add("b-plugin@b-market")
        backend.resources.publish()
        Path(config["plugins"][0]["path"]).rename(tmp_path / "retained-marketplace.json")
        backend.resources.prepare()
        calls = []
        backend.resources.register(SimpleNamespace(
            request=lambda method, params, **kwargs: calls.append((method, params)) or {},
        ))
        assert calls == [
            ("config/read", {"includeLayers": False, "cwd": str(tmp_path)}),
            ("config/value/write", {"keyPath": 'plugins."b-plugin@b-market".enabled',
                                    "value": False, "mergeStrategy": "replace"}),
            ("skills/extraRoots/set", {"extraRoots": [config["skill_roots"][0]["path"]]}),
        ]
        assert (tmp_path / "retained-marketplace.json").exists()


def test_snapshot_dependency_failure_and_recovery_are_visible_and_deduplicated(tmp_path):
    manifest, config = make_resources(tmp_path)
    provider = SimpleNamespace(options={"external_resources": str(manifest)}, environment={})
    status = ["failed"]
    with SessionJournal(tmp_path, "resource-dependency") as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        backend.resources.prepare()
        roots = backend.resources.config.selected_roots()
        backend._bind_thread("start", {"id": "native-thread"}, expected=None, roots=roots)
        backend.resources.bound = roots
        backend.runtime = SimpleNamespace(cwd=str(tmp_path))
        def request(method, params, **kwargs):
            if method == "skills/list":
                return {"data": [{"skills": [{"name": "external", "path": str(
                    Path(config["skill_roots"][0]["path"]) / "b-user-proof/SKILL.md"),
                    "enabled": True, "scope": "user", "dependencies": {"tools": [
                        {"type": "mcp", "value": "missing"}]}}], "errors": []}]}
            if method == "plugin/installed":
                return {"marketplaces": [{"plugins": [{"id": "b-plugin@b-market",
                    "installed": True, "enabled": True}]}]}
            if method == "plugin/read":
                return {"plugin": {"mcpServers": ["plugin-tool"], "apps": [], "hooks": []}}
            if method == "mcpServerStatus/list":
                return {"data": [{"name": "plugin-tool", "pluginId": "b-plugin@b-market",
                    "runtimeStatus": status[0], "toolsError": None, "tools": {}}]}
            raise AssertionError(method)
        rpc = SimpleNamespace(request=request)
        first = backend.resources.inspect(rpc)
        assert first["rows"][1]["status"] == "dependency_missing"
        assert first["dependencies"][0]["status"] == "missing"
        count = len(journal.events())
        assert backend.resources.inspect(rpc) == first
        assert len(journal.events()) == count
        status[0] = "connected"
        second = backend.resources.inspect(rpc)
        assert second["rows"][1]["status"] == "available"
        assert first["rows"][1]["status"] == "dependency_missing"
        assert len(journal.events()) == count + 1
        def timeout(*args, **kwargs):
            raise RpcError("Inspection timed out")
        failed = backend.resources.inspect(SimpleNamespace(request=timeout))
        assert failed["status"] == "partial"
        assert all(r["status"] == "unverified" for r in failed["rows"])
        assert "servers" not in failed["rows"][1]
        assert backend.resources.inspect(rpc)["rows"][1]["status"] == "available"
        manifest.write_text("broken")
        backend.resources.prepare()
        assert backend.resources.published["configuration"]["defaults"]["status"] == "unavailable"
        assert len(backend.resources.published["rows"]) == 3
        backend.runtime = None


def test_builtin_mcp_inventory_follows_the_running_session(tmp_path):
    manifest, _ = make_resources(tmp_path)
    provider = SimpleNamespace(options={"external_resources": str(manifest)}, environment={})
    with SessionJournal(tmp_path, "resource-builtin") as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        backend.resources.prepare()
        assert backend.resources.published["builtin_mcp"] == [{"id": "sico_ic"}]
        roots = backend.resources.config.selected_roots()
        backend._bind_thread("start", {"id": "native-thread"}, expected=None, roots=roots)
        backend.resources.bound = roots
        backend.runtime = SimpleNamespace(cwd=str(tmp_path))
        def request(method, params, **kwargs):
            if method == "skills/list":
                return {"data": [{"skills": [], "errors": []}]}
            if method == "plugin/installed":
                return {"marketplaces": []}
            if method == "mcpServerStatus/list":
                return {"data": [
                    {"name": "sico_ic", "tools": {}},
                    {"name": "sico_ae", "tools": {}},
                    {"name": "sico_knowledge", "tools": {}},
                    {"name": "plugin-tool", "pluginId": "b-plugin@b-market", "tools": {}},
                ]}
            raise AssertionError(method)
        snapshot = backend.resources.inspect(SimpleNamespace(request=request))
        assert snapshot["builtin_mcp"] == [
            {"id": "sico_ic"}, {"id": "sico_ae"}, {"id": "sico_knowledge"}]
        backend.runtime = None


def test_malformed_or_unreadable_explicit_manifest_fails_visibly(tmp_path):
    with SessionJournal(tmp_path, "resource-error") as journal:
        provider = SimpleNamespace(options={"external_resources": str(tmp_path / "missing")},
                                   environment={})
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        with pytest.raises(RpcError, match="清单不可读"):
            backend.resources.prepare()
        assert backend.runtime is None
        assert journal.events()[-1]["kind"] == "codex.resources.updated"


def test_registration_failure_publishes_unavailable_without_installing(tmp_path):
    manifest, _ = make_resources(tmp_path)
    provider = SimpleNamespace(options={"external_resources": str(manifest)}, environment={})
    with SessionJournal(tmp_path, "resource-registration") as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        backend.features.save({"plugins": {"features.plugins": True}})
        backend.resources.prepare()
        calls = []
        def request(method, params, **kwargs):
            calls.append(method)
            raise RpcError("Resource RPC unavailable")
        with pytest.raises(RpcError, match="注册未完成"):
            backend.resources.register(SimpleNamespace(request=request))
        assert calls == ["config/read"]
        assert backend.resources.published["status"] == "unavailable"
        assert backend.resources.published["issues"]


def test_selected_plugin_fails_visibly_before_any_registration_rpc(tmp_path):
    manifest, _ = make_resources(tmp_path)
    provider = SimpleNamespace(options={"external_resources": str(manifest)}, environment={})
    with SessionJournal(tmp_path, "plugins-disabled") as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        backend.resources.prepare()
        calls = []
        with pytest.raises(RpcError, match="GitHub"):
            backend.resources.register(SimpleNamespace(
                request=lambda *args, **kwargs: calls.append(args)))
        assert not calls and backend.runtime is None
        snapshot = backend.resources.published
        assert snapshot["status"] == "unavailable"
        row = next(r for r in snapshot["rows"] if r["kind"] == "plugins")
        assert row["status"] == "unavailable" and "plugins 特性已关闭" in row["message"]
        assert snapshot["configuration"]["effective"]["manifest"]["plugins"]
