"""AS3 independent resources through the pinned native CLI and controlled gateways."""

import json
import sys

import pytest
from sico.agent_resources.schema import upgrade_manifest
from sico.codex.backend import CodexBackend
from sico.service.frontend_session import SessionToken
from sico.storage.journal import SessionJournal
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
    registry,
    settings,
)
from test_codex_resources_runtime import make_resources, output, tool_call


def server(root, identity="external"):
    script = root / "external_server.py"
    script.write_text('''import json,os,sys
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 if m['method']=='initialize':
  r={'protocolVersion':m['params']['protocolVersion'],'capabilities':{'tools':{}},
     'serverInfo':{'name':'external','version':'1'}}
 elif m['method']=='tools/list':
  r={'tools':[{'name':'proof','description':'Controlled read-only check.',
   'inputSchema':{'type':'object','properties':{}},'annotations':{'readOnlyHint':True}},
   {'name':'hidden','description':'Not selected.','inputSchema':{'type':'object'}}]}
 elif m['method']=='tools/call':
  assert os.environ.get('PRIVATE_TOKEN') == 'external-secret-sentinel'
  assert 'SICO_MODEL_KEY' not in os.environ and 'SICO_API_KEY' not in os.environ
  assert not any(k.startswith('SICO_RESOURCE_') for k in os.environ)
  r={'content':[{'type':'text','text':'EXTERNAL_MCP_PROOF'}]}
 else: r={}
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)
''')
    return dict(id=identity, name="External", source="project", enabled=True, transport="stdio",
                command=sys.executable, args=[str(script)], cwd=str(root),
                env_refs={"PRIVATE_TOKEN": "EXTERNAL_TOKEN"}, enabled_tools=["proof"],
                startup_timeout_sec=3, tool_timeout_sec=3, required=True)


@pytest.mark.parametrize("tool_format", ["native", "flat"])
def test_native_independent_mcp_credentials_filter_and_call(
        tmp_path, gateway, native_codex, tool_format):  # noqa: F811
    row = server(tmp_path)
    manifest = tmp_path / "resources.json"
    manifest.write_text(json.dumps({"version": 2, "mcp_servers": [row]}))
    provider = settings(gateway, native_codex, tool_format)
    provider.options["external_resources"] = str(manifest)
    provider.environment["EXTERNAL_TOKEN"] = "external-secret-sentinel"
    gateway.responses.put(lambda request: tool_call(request, "mcp__external", "proof", "proof", {}))
    gateway.responses.put(answer("Checked external tools."))
    with SessionJournal(tmp_path, "mcp") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            events = list(backend.run("Call the approved external proof tool."))
            assert events[-1]["kind"] == "task.completed", events[-4:]
            inventory = backend.resources.published["mcp_servers"][0]
            assert inventory["status"] == "available" and inventory["tools"] == ["proof"]
            assert "user rejected MCP tool call" in str(output(gateway.requests[-1], "proof"))
            result = backend.runtime.rpc.request("mcpServer/tool/call", {
                "threadId": backend.thread_id, "server": "external",
                "tool": "proof", "arguments": {}})
            assert "EXTERNAL_MCP_PROOF" in json.dumps(result)
            assert "external-secret-sentinel" not in json.dumps(journal.events())
            config_text = (backend.runtime.home / "config.toml").read_text()
            assert "external-secret-sentinel" not in config_text
            assert not (backend.runtime.home / ".tmp/plugins.sync.lock").exists()
            assert not (backend.runtime.home / ".tmp/plugins.sha").exists()
        finally:
            backend.close()


@pytest.mark.parametrize("plugin", [False, True])
def test_skill_overrides_bind_definition_path_and_restore_old_choice(
        tmp_path, gateway, native_codex, plugin):  # noqa: F811
    manifest, value = make_resources(tmp_path)
    value = upgrade_manifest(value)
    value["capability_roots"] = []
    if not plugin:
        value["plugins"] = []
    path = (tmp_path / "market/plugins/b-plugin/skills/b-plugin-proof/SKILL.md" if plugin else
            tmp_path / "skills/b-user-proof/SKILL.md")
    value["skill_overrides"] = [{"path": str(path), "enabled": False}]
    manifest.write_text(json.dumps(value))
    provider = settings(gateway, native_codex, "native")
    provider.options["external_resources"] = str(manifest)
    for index in range(2):
        with SessionJournal(tmp_path, "skills") as journal:
            backend = CodexBackend(provider, registry([]), journal, CONTEXT)
            if plugin:
                backend.features.save({"plugins": {"features.plugins": True}})
            try:
                gateway.responses.put(answer("Selected skills checked."))
                events = list(backend.run("Check selected skill availability."))
                assert events[-1]["kind"] == "task.completed", events[-4:]
                skill = next(s for s in backend.resources.published["skills"]
                             if s["definition_path"] == str(path))
                assert skill["enabled"] is False
                if plugin:
                    assert skill["path"] != str(path)
                assert backend.resources.published["configuration"]["pending"] == bool(index)
            finally:
                backend.close()
        value["skill_overrides"][0]["enabled"] = True
        manifest.write_text(json.dumps(value))
    with SessionJournal(tmp_path, "new-skills") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        if plugin:
            backend.features.save({"plugins": {"features.plugins": True}})
        try:
            gateway.responses.put(answer("New defaults checked."))
            assert list(backend.run("Check new defaults."))[-1]["kind"] == "task.completed"
            selected = next(s for s in backend.resources.published["skills"]
                            if s["definition_path"] == str(path))
            assert selected["enabled"] is True
        finally:
            backend.close()


def test_plugin_mcp_collision_prevents_thread_and_model(tmp_path, gateway, native_codex):  # noqa: F811
    manifest, value = make_resources(tmp_path)
    value = upgrade_manifest(value)
    row = server(tmp_path, "collision")
    value["mcp_servers"] = [row]
    manifest.write_text(json.dumps(value))
    (tmp_path / "market/plugins/b-plugin/.mcp.json").write_text(json.dumps({"mcpServers": {
        "collision": {"command": sys.executable, "args": row["args"]}}}))
    provider = settings(gateway, native_codex, "native")
    provider.options["external_resources"] = str(manifest)
    provider.environment["EXTERNAL_TOKEN"] = "external-secret-sentinel"
    with SessionJournal(tmp_path, "collision") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        backend.features.save({"plugins": {"features.plugins": True}})
        try:
            list(backend.run("Inspect resources."))
            assert not backend.thread_id and backend.runtime is None and not gateway.requests
            assert backend.resources.published["status"] == "unavailable"
        finally:
            backend.close()


@pytest.mark.parametrize("tool_format", ["native", "flat"])
def test_enabled_skill_input_uses_full_path_and_disabled_choice_rejected(
        tmp_path, gateway, native_codex, tool_format):  # noqa: F811
    from sico.storage.input_assets import prepare_inputs

    manifest, value = make_resources(tmp_path)
    value = upgrade_manifest(value)
    value.update(capability_roots=[], plugins=[])
    source = tmp_path / "skills/b-user-proof/SKILL.md"
    same = tmp_path / "second/b-user-proof/SKILL.md"
    same.parent.mkdir(parents=True)
    same.write_text(source.read_text().replace("B_RESOURCE_PROOF", "SECOND_SKILL_PROOF"))
    value["skill_roots"].append({"id": "second", "path": str(same.parent.parent),
                                  "source": "user", "access": "read", "enabled": True})
    value["skill_overrides"] = [{"path": str(source), "enabled": False}]
    manifest.write_text(json.dumps(value))
    provider = settings(gateway, native_codex, tool_format)
    provider.options["external_resources"] = str(manifest)
    with SessionJournal(tmp_path, "explicit") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            skills = backend.turn_options.status()["skills"]
            assert len([s for s in skills if s["name"] == "b-user-proof"]) == 2
            with pytest.raises(ValueError, match="enabled inventory"):
                prepare_inputs(journal, [{"type": "skill", "name": "b-user-proof",
                                         "path": str(source)}], skills)
            inputs = prepare_inputs(journal, [{"type": "skill", "name": "b-user-proof",
                                              "path": str(same)}], skills)
            gateway.responses.put(answer("Explicit skill loaded."))
            events = list(backend.run("Use the selected skill.", inputs=inputs))
            assert events[-1]["kind"] == "task.completed", events[-4:]
            assert "SECOND_SKILL_PROOF" in json.dumps(gateway.requests[-1])
            assert "B_RESOURCE_PROOF" not in json.dumps(gateway.requests[-1])
            backend.turn_options.catalog["skills"] = skills
            backend._route_notification({"method": "skills/changed"}, consuming=True)
            assert backend.resources.published["inventory_stale"]
            assert not backend.turn_options.catalog["skills"]
        finally:
            backend.close()


@pytest.mark.parametrize("tool_format", ["native", "flat"])
def test_independent_mcp_model_call_follows_existing_interactive_approval(
        tmp_path, gateway, native_codex, tool_format):  # noqa: F811
    from agent_sessions_harness import DesktopSessions
    from sico.service.backend import create_backend
    from sico.service.controller import SessionController
    from test_codex_elicitation_runtime import scope_for, wait_for

    manifest = tmp_path / "resources.json"
    manifest.write_text(json.dumps({"version": 2, "mcp_servers": [server(tmp_path)]}))
    provider = settings(gateway, native_codex, tool_format)
    provider.options["external_resources"] = str(manifest)
    provider.environment["EXTERNAL_TOKEN"] = "external-secret-sentinel"
    gateway.responses.put(lambda request: tool_call(request, "mcp__external", "proof", "proof", {}))
    gateway.responses.put(answer("External evidence received."))
    with SessionJournal(tmp_path, "approved") as journal:
        backend = create_backend(provider, registry([]), journal, CONTEXT)
        controller = SessionController(backend)
        sessions = DesktopSessions(controller)
        try:
            controller.submit("Read the external proof.")
            wait_for(lambda: bool(backend.audit.elicitations._pending()) or not controller.busy)
            assert backend.audit.elicitations._pending(), backend.state.task
            row = next(r for r in backend.audit.index.audits.values() if "elicitation" in r)
            sessions.worker.command(SessionToken(controller.session_id, controller.runtime_id),
                "elicitation", row["id"],
                scope=scope_for(controller, row), response={"action": "accept", "content": {}},
                reply_id="allow-external").result(3)
            wait_for(lambda: not controller.busy)
            assert backend.state.task["status"] == "completed", backend.state.task
            assert "EXTERNAL_MCP_PROOF" in str(output(gateway.requests[-1], "proof"))
            assert "external-secret-sentinel" not in json.dumps(journal.events())
        finally:
            sessions.close()
            assert sessions.wait(8)
