"""External resources through pinned Codex 0.162.1 and a controlled local gateway."""

import json
import sys
from pathlib import Path

import pytest
from sico.codex.backend import CodexBackend
from sico.codex.rpc import AppServer
from sico.storage.journal import SessionJournal
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
    registry,
    settings,
    sse,
)


def plugin_backend(provider, journal):
    backend = CodexBackend(provider, registry([]), journal, CONTEXT)
    backend.features.save({"plugins": {"features.plugins": True}})
    return backend


def make_resources(root):
    def skill(path, name):
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            "---\nname: " + name + "\ndescription: Controlled resource proof.\n---\n"
            "B_RESOURCE_PROOF: read this local resource only.\n",
        )
    skills, capability, market = root / "skills", root / "capability", root / "market"
    skill(skills / "b-user-proof", "b-user-proof")
    skill(capability / "skills/b-cap-proof", "b-cap-proof")
    plugin = market / "plugins/b-plugin"
    skill(plugin / "skills/b-plugin-proof", "b-plugin-proof")
    (plugin / ".codex-plugin").mkdir()
    (plugin / ".codex-plugin/plugin.json").write_text(json.dumps({
        "name": "b-plugin", "version": "1.0.0", "description": "Controlled plugin",
        "skills": "./skills/",
    }))
    (market / ".agents/plugins").mkdir(parents=True)
    marketplace = market / ".agents/plugins/marketplace.json"
    marketplace.write_text(json.dumps({"name": "b-market", "plugins": [{
        "name": "b-plugin", "source": {"source": "local", "path": "./plugins/b-plugin"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"},
    }]}))
    config = {"version": 1,
              "skill_roots": [{"id": "user", "path": str(skills),
                               "source": "user", "access": "read"}],
              "plugins": [{"id": "plugin", "path": str(marketplace), "name": "b-plugin",
                           "source": "project", "access": "execute"}],
              "capability_roots": [{"id": "cap", "path": str(capability),
                                    "source": "project", "access": "execute"}]}
    manifest = root / "resources.json"
    manifest.write_text(json.dumps(config))
    return manifest, config


def tool_call(request, namespace, name, call_id, arguments):
    for group in request["tools"]:
        for tool in group.get("tools", [group]):
            if (group.get("name") == namespace and tool.get("name") == name
                    or tool.get("name") == namespace + "__" + name):
                call = {"type": "function_call", "id": "fc_" + call_id, "call_id": call_id,
                        "name": tool["name"], "arguments": json.dumps(arguments)}
                if group.get("type") == "namespace":
                    call["namespace"] = namespace
                return sse(call)
    raise AssertionError("Missing tool " + namespace + "." + name)


def output(request, call_id):
    return next(i["output"] for i in request["input"]
                if i.get("type") == "function_call_output" and i.get("call_id") == call_id)


def cap_read(request):
    value = json.loads(output(request, "skills-list"))
    # Public tool content wraps the schema inventory as one text block.
    if isinstance(value, dict) and "content" in value:
        value = json.loads(value["content"][0]["text"])
    selected = next(s for s in value["skills"] if s["name"] == "b-cap-proof")
    return tool_call(request, "skills", "read", "skills-read", {"package": selected["package"]})


@pytest.mark.parametrize("tool_format", ["native", "flat"])
def test_actual_external_resources_are_available_and_isolated(
    tmp_path, gateway, native_codex, tool_format, monkeypatch,  # noqa: F811
):
    manifest, config = make_resources(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    provider = settings(gateway, native_codex, tool_format)
    provider.options["external_resources"] = str(manifest)
    personal = tmp_path / "personal-codex"
    personal.mkdir()
    (personal / "auth.json").write_text('{"sentinel": "DO_NOT_INHERIT_AUTH"}')
    provider.environment["CODEX_HOME"] = str(personal)
    calls = []
    original_request = AppServer.request

    def request(self, method, params, **kwargs):
        calls.append(method)
        return original_request(self, method, params, **kwargs)

    monkeypatch.setattr(AppServer, "request", request)
    gateway.responses.put(lambda request: tool_call(
        request, "skills", "list", "skills-list", {"authority": {"kind": "executor"}},
    ))
    gateway.responses.put(cap_read)
    gateway.responses.put(answer("Resource inventory received."))
    with SessionJournal(project, "resources") as journal:
        backend = plugin_backend(provider, journal)
        try:
            events = list(backend.run("List skills from the selected capability root."))
            assert events[-1]["kind"] == "task.completed", events[-5:]
            snapshot = backend.resources.snapshot
            (tmp_path / (tool_format + "-inventory.json")).write_text(
                json.dumps(snapshot, indent=2),
            )
            assert {s["name"] for s in snapshot["skills"]} >= {
                "b-user-proof", "b-plugin:b-plugin-proof",
            }
            assert all(r["status"] in {"available", "selected"} for r in snapshot["rows"]), snapshot
            request = json.dumps(gateway.requests[0])
            assert all(name in request for name in
                       ("b-user-proof", "b-plugin-proof", "b-cap-proof"))
            assert any(i.get("type") == "function_call_output" and "b-cap-proof" in str(i)
                       for i in gateway.requests[1]["input"])
            assert "B_RESOURCE_PROOF" in output(gateway.requests[2], "skills-read")
            assert backend.runtime.home == journal.root / "codex"
            assert not (backend.runtime.home / "auth.json").exists()
            assert "DO_NOT_INHERIT_AUTH" not in json.dumps(journal.events())
            assert Path(config["skill_roots"][0]["path"]).is_dir()
            assert not (backend.runtime.home / "skills/b-user-proof").exists()
            thread_id = backend.thread_id
        finally:
            backend.close()
    manifest.write_text('{"version":2}')
    with SessionJournal(project, "resources") as journal:
        backend = plugin_backend(provider, journal)
        try:
            gateway.responses.put(answer("Resumed resource inventory."))
            events = list(backend.run("Inspect the same available resources."))
            assert events[-1]["kind"] == "task.completed", events[-5:]
            assert backend.thread_id == thread_id
            assert "b-cap-proof" in json.dumps(gateway.requests[-1])
            assert len(gateway.requests) == 4
            assert calls.count("plugin/install") == 1
            assert backend.resources.published["configuration"]["pending"]
        finally:
            backend.close()
    with SessionJournal(project, "new-resources") as journal:
        backend = plugin_backend(provider, journal)
        try:
            gateway.responses.put(answer("New selection checked."))
            events = list(backend.run("Inspect resources in this new session."))
            assert events[-1]["kind"] == "task.completed", events[-5:]
            assert backend.resources.config.rows == []
            assert "b-cap-proof" not in json.dumps(gateway.requests[-1])
            assert "b-plugin-proof" not in json.dumps(gateway.requests[-1])
            assert not backend.resources.published["configuration"]["pending"]
        finally:
            backend.close()
    assert (personal / "auth.json").read_text() == '{"sentinel": "DO_NOT_INHERIT_AUTH"}'


@pytest.mark.parametrize("corrupt_snapshot", [False, True])
def test_missing_manifest_allows_metadata_without_reloading_resources(
    tmp_path, gateway, native_codex, monkeypatch, corrupt_snapshot,  # noqa: F811
):
    manifest, _ = make_resources(tmp_path)
    provider = settings(gateway, native_codex, "native")
    provider.options["external_resources"] = str(manifest)
    with SessionJournal(tmp_path, "resource-metadata") as journal:
        backend = plugin_backend(provider, journal)
        try:
            gateway.responses.put(answer("Resource review completed."))
            assert list(backend.run("Review configured resources."))[-1]["kind"] == "task.completed"
            if corrupt_snapshot:
                backend._event("codex.resources.selected", {"damaged": True})
        finally:
            backend.close()
    manifest.unlink()
    calls = []
    original = AppServer.request

    def request(self, method, params, **kwargs):
        calls.append(method)
        return original(self, method, params, **kwargs)

    monkeypatch.setattr(AppServer, "request", request)
    with SessionJournal(tmp_path, "resource-metadata") as journal:
        backend = plugin_backend(provider, journal)
        try:
            backend.set_thread_name("Retained resource review")
            assert journal.name_metadata["name"] == "Retained resource review"
            assert backend.runtime is None
            backend.delete_thread()
            assert journal.events()[-1]["kind"] == "codex.thread.deleted"
            assert backend.runtime is None
            assert calls == ["initialize", "thread/name/set", "initialize", "thread/delete"]
            assert len(gateway.requests) == 1
        finally:
            backend.close()


@pytest.mark.parametrize("change", ["removed", "missing", "version"])
def test_actual_plugin_lifecycle_on_resume(tmp_path, gateway, native_codex, change):  # noqa: F811
    manifest, config = make_resources(tmp_path)
    config["capability_roots"] = []
    manifest.write_text(json.dumps(config))
    selected_manifest = manifest.read_text()
    provider = settings(gateway, native_codex, "native")
    provider.options["external_resources"] = str(manifest)
    with SessionJournal(tmp_path, "plugin-lifecycle") as journal:
        backend = plugin_backend(provider, journal)
        try:
            gateway.responses.put(answer("Plugin ready."))
            assert list(backend.run("Inspect available resources."))[-1]["kind"] == "task.completed"
            assert "b-plugin-proof" in json.dumps(gateway.requests[-1])
            thread = backend.thread_id
        finally:
            backend.close()
    if change == "removed":
        config["plugins"] = []
        config["skill_roots"] = []
        manifest.write_text(json.dumps(config))
    elif change == "missing":
        Path(config["plugins"][0]["path"]).rename(tmp_path / "retained-marketplace.json")
    else:
        bundle = tmp_path / "market/plugins/b-plugin"
        data = json.loads((bundle / ".codex-plugin/plugin.json").read_text())
        data["version"] = "2.0.0"
        (bundle / ".codex-plugin/plugin.json").write_text(json.dumps(data))
        (bundle / "skills/b-plugin-proof/SKILL.md").write_text(
            "---\nname: b-updated-proof\ndescription: Updated resource.\n---\nUpdated.\n",
        )
    with SessionJournal(tmp_path, "plugin-lifecycle") as journal:
        backend = plugin_backend(provider, journal)
        try:
            if change != "version":
                gateway.responses.put(answer("Resource changes checked."))
            events = list(backend.run("Use currently selected resources only."))
            if change == "version":
                assert events[-1]["kind"] == "task.needs_reconcile", events[-5:]
                assert backend.runtime is None and len(gateway.requests) == 1
                assert backend.resources.published["configuration"]["drift"][0]["blocked"]
            else:
                assert events[-1]["kind"] == "task.completed", events[-5:]
                assert backend.thread_id == thread
                # Historical messages persist; native inventory proves current availability.
                skills = backend.runtime.rpc.request("skills/list", {
                    "cwds": [str(tmp_path)], "forceReload": True,
                })["data"][0]["skills"]
                names = {s["name"] for s in skills if s["enabled"]}
                if change == "removed":
                    assert "b-plugin:b-plugin-proof" in names
                    assert "b-user-proof" in names
                    assert backend.resources.published["configuration"]["pending"]
                else:
                    assert "b-plugin:b-plugin-proof" not in names
        finally:
            backend.close()
    if change == "version":
        with SessionJournal(tmp_path, "new-plugin-version") as journal:
            backend = plugin_backend(provider, journal)
            try:
                gateway.responses.put(answer("Updated version selected."))
                assert list(backend.run("Inspect updated plugin."))[-1]["kind"] == "task.completed"
                assert "b-plugin:b-updated-proof" in {
                    s["name"] for s in backend.resources.published["skills"] if s["enabled"]}
            finally:
                backend.close()
    if change == "removed":
        manifest.write_text(selected_manifest)
        with SessionJournal(tmp_path, "plugin-lifecycle") as journal:
            backend = plugin_backend(provider, journal)
            try:
                gateway.responses.put(answer("Selected plugin restored."))
                events = list(backend.run("Check restored resources."))
                assert events[-1]["kind"] == "task.completed"
                assert backend.thread_id == thread
                assert "b-plugin:b-plugin-proof" in {
                    s["name"] for s in backend.resources.published["skills"] if s["enabled"]
                }
            finally:
                backend.close()


def test_resources_not_configured_do_not_inherit_personal_plugins(tmp_path, gateway, native_codex):  # noqa: F811
    provider = settings(gateway, native_codex, "native")
    with SessionJournal(tmp_path, "no-resources") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            gateway.responses.put(answer("No external selection."))
            assert list(backend.run("Inspect project."))[-1]["kind"] == "task.completed"
            assert not backend.resources.config.configured
            assert backend.runtime.rpc.request("plugin/installed", {})["marketplaces"] == []
            assert "b-user-proof" not in json.dumps(gateway.requests)
        finally:
            backend.close()


@pytest.mark.parametrize("working,approved", [(True, True), (True, False), (False, False)])
def test_actual_plugin_mcp_dependency_status_and_call(
    tmp_path, gateway, native_codex, working, approved,  # noqa: F811
):
    manifest, config = make_resources(tmp_path)
    config["capability_roots"] = []
    manifest.write_text(json.dumps(config))
    bundle = tmp_path / "market/plugins/b-plugin"
    server = bundle / "proof_server.py"
    server.write_text('''import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if 'id' not in message:
        continue
    method = message['method']
    if method == 'initialize':
        result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'proof', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'resource_probe', 'description': 'Read controlled proof.',
                  'inputSchema': {'type': 'object', 'properties': {}}}]}
    elif method == 'tools/call':
        result = {'content': [{'type': 'text', 'text': 'PLUGIN_MCP_PROOF'}]}
    else:
        result = {}
    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
''')
    (bundle / ".mcp.json").write_text(json.dumps({"mcpServers": {"proof": {
        "command": sys.executable if working else str(tmp_path / "missing-executable"),
        "args": [str(server)], "startup_timeout_sec": 2,
    }}}))
    provider = settings(gateway, native_codex, "native")
    provider.options["external_resources"] = str(manifest)
    if working:
        def call(request):
            for group in request["tools"]:
                if any(t.get("name") == "resource_probe" for t in group.get("tools", [])):
                    return tool_call(request, group["name"], "resource_probe", "plugin-proof", {})
            raise AssertionError("Plugin MCP not exposed to the model")
        gateway.responses.put(call)
    gateway.responses.put(answer("Plugin dependency inspected."))
    with SessionJournal(tmp_path, "plugin-mcp") as journal:
        if approved:
            home = journal.directory / "codex"
            home.mkdir(mode=0o700)
            (home / "config.toml").write_text(
                '[plugins."b-plugin@b-market".mcp_servers.proof.tools.resource_probe]\n'
                'approval_mode = "approve"\n')
        backend = plugin_backend(provider, journal)
        try:
            events = list(backend.run("Call the resource probe if it is available."))
            assert events[-1]["kind"] == "task.completed", events[-5:]
            snapshot = backend.resources.inspect(backend.runtime.rpc)
            row = next(r for r in snapshot["rows"] if r["kind"] == "plugins")
            assert row["status"] == ("available" if working else "dependency_missing"), row
            assert row["servers"], snapshot
            if working:
                proof = output(gateway.requests[-1], "plugin-proof")
                if approved:
                    assert "PLUGIN_MCP_PROOF" in json.dumps(proof)
                else:
                    assert "user rejected MCP tool call" in json.dumps(proof)
                    assert "PLUGIN_MCP_PROOF" not in json.dumps(proof)
            (tmp_path / "plugin-mcp-inventory.json").write_text(json.dumps(snapshot, indent=2))
        finally:
            backend.close()
