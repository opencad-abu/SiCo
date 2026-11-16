"""Original 0.154 MCP server requests with isolated resources and controlled model output."""

import json
import sys
import time

import pytest
from agent_sessions_harness import DesktopSessions
from sico.codex.rpc import AppServer
from sico.service.backend import create_backend
from sico.service.controller import SessionController
from sico.service.detail_content import DetailContent
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

FORM = {"type": "object", "properties": {
    "label": {"type": "string", "minLength": 2, "maxLength": 30},
    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    "enabled": {"type": "boolean"},
    "mode": {"type": "string", "oneOf": [
        {"const": "read", "title": "只读取"}, {"const": "compare", "title": "做对照"}]},
    "points": {"type": "array", "items": {"type": "string", "enum": ["input", "output"]}},
    "note": {"type": "string"},
}, "required": ["label", "limit", "enabled", "mode", "points"]}
CONTENT = {"label": "revised", "limit": 17, "enabled": False,
           "mode": "compare", "points": ["output"]}


def wait_for(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "MCP interaction did not reach expected state"
        time.sleep(0.01)


def scope_for(controller, row):
    return {"session_id": controller.session_id, "runtime_id": controller.runtime_id,
            "task_id": row["task_id"], "context": row["context"], "binding": row["binding"]}


def resources(tmp_path, mode, approval="approve"):
    manifest, config = make_resources(tmp_path)
    config["skill_roots"], config["capability_roots"] = [], []
    manifest.write_text(json.dumps(config))
    bundle = tmp_path / "market/plugins/b-plugin"
    request = {"mode": mode, "message": "Please provide the revised inspection scope."}
    if mode == "form":
        request["requestedSchema"] = FORM
    else:
        request.update(elicitationId="url-proof", url="https://example.invalid/review")
    server = bundle / "interaction_server.py"
    server.write_text('''import json, sys
request = json.loads(sys.argv[1])
pending = None
for line in sys.stdin:
    message = json.loads(line)
    if 'id' not in message:
        continue
    if 'method' not in message:
        if message['id'] == 'interaction-one':
            result = {'content': [{'type': 'text', 'text': json.dumps(message)}]}
            print(json.dumps({'jsonrpc': '2.0', 'id': pending, 'result': result}), flush=True)
        continue
    method = message['method']
    if method == 'initialize':
        result = {'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'interaction-proof', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'interaction_probe', 'description': 'Inspect revised scope.',
                  'inputSchema': {'type': 'object', 'properties': {}}}]}
    elif method == 'tools/call':
        pending = message['id']
        print(json.dumps({'jsonrpc': '2.0', 'id': 'interaction-one',
                          'method': 'elicitation/create', 'params': request}), flush=True)
        continue
    else:
        result = {}
    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
''')
    (bundle / ".mcp.json").write_text(json.dumps({"mcpServers": {"interaction": {
        "command": sys.executable, "args": [str(server), json.dumps(request)],
        "startup_timeout_sec": 3,
        "default_tools_approval_mode": approval,
    }}}))
    return manifest


@pytest.mark.parametrize("tool_format,mode,action,approval", [
    ("native", "form", "accept", "approve"), ("flat", "form", "accept", "approve"),
    ("native", "form", "decline", "approve"), ("flat", "form", "cancel", "approve"),
    ("native", "url", "accept", "approve"), ("flat", "url", "cancel", "approve"),
    ("native", "form", "accept", "prompt"), ("flat", "form", "accept", "prompt"),
])
def test_actual_mcp_interaction_round_trip(
    tmp_path, gateway, native_codex, tool_format, mode, action, approval, monkeypatch,  # noqa: F811
):
    provider = settings(gateway, native_codex, tool_format)
    provider.options["external_resources"] = str(resources(tmp_path, mode, approval))

    def call(request):
        for group in request["tools"]:
            for tool in group.get("tools", [group]):
                if tool.get("name", "").endswith("interaction_probe"):
                    namespace = (group["name"] if group.get("type") == "namespace"
                                 else tool["name"].rsplit("__", 1)[0])
                    return tool_call(request, namespace, "interaction_probe", "interaction", {})
        raise AssertionError("Controlled MCP tool was not available")

    calls = []
    original = AppServer.request
    def rpc(self, method, params, **kwargs):
        calls.append(method)
        return original(self, method, params, **kwargs)
    monkeypatch.setattr(AppServer, "request", rpc)
    gateway.responses.put(call)
    gateway.responses.put(answer("Interaction outcome received."))
    with SessionJournal(tmp_path, "elicitation-native") as journal:
        backend = create_backend(provider, registry([]), journal, CONTEXT)
        backend.features.save({"plugins": {"features.plugins": True}})
        controller = SessionController(backend)
        sessions = DesktopSessions(controller)
        try:
            controller.submit("Inspect the supplied scope and ask for the required information.")
            wait_for(lambda: bool(backend.audit.elicitations._pending()) or not controller.busy)
            assert backend.audit.elicitations._pending(), {
                "task": backend.state.task, "output": output(gateway.requests[-1], "interaction")}
            row = next(r for r in backend.audit.index.audits.values() if "elicitation" in r)
            if approval == "prompt":
                assert row["elicitation"]["schema"]["properties"] == {}
                assert sessions.worker.command(
                    SessionToken(controller.session_id, controller.runtime_id),
                    "elicitation", row["id"],
                    scope=scope_for(controller, row), response={"action": "accept", "content": {}},
                    reply_id="allow-tool").result(3)
                wait_for(lambda: len(backend.audit.elicitations.seen) == 2)
                row = next(reversed(backend.audit.index.audits.values()))
            captured = scope_for(controller, row)
            result = {"action": action}
            if action == "accept" and mode == "form":
                result["content"] = CONTENT
            assert sessions.worker.command(
                SessionToken(controller.session_id, controller.runtime_id),
                "elicitation", row["id"],
                scope=captured, response=result, reply_id="reply").result(3)
            wait_for(lambda: not controller.busy)
            assert backend.state.task["status"] == "completed", backend.state.task
            assert row["status"] == "answered"
            assert not controller.inbox.pending and len(controller.history.rows) == 1
            proof = output(gateway.requests[-1], "interaction")
            texts = ([part.get("text", "") for part in proof] if isinstance(proof, list)
                     else [proof])
            echoed = next(json.loads(t) for t in texts if t.startswith('{"jsonrpc"'))
            assert echoed["result"]["action"] == action
            if mode == "form":
                assert echoed["result"].get("content") == result.get("content")
            else:
                # Original 0.154 normalizes accepted URL content to an empty object.
                assert echoed["result"].get("content") in (None, {})
            assert "服务结果另行核对" in DetailContent(backend.audit.index).prepare(
                "audit", row["id"])["html"]
            assert len(gateway.requests) == 2
            assert calls.count("turn/start") == 1
            assert not any(name.startswith("account/") for name in calls)
            assert not (backend.runtime.home / "auth.json").exists()
            (tmp_path / "interaction-proof.json").write_text(json.dumps({
                "scope": captured, "reply": result, "row": row, "output": proof,
                "rpc_methods": calls,
            }, ensure_ascii=False, indent=2))
        finally:
            sessions.close()
            assert sessions.wait(8)


@pytest.mark.parametrize("tool_format", ["native", "flat"])
def test_granular_interactions_preserve_authorized_command_patch_and_resume(
    tmp_path, gateway, native_codex, tool_format,  # noqa: F811
):
    from test_codex_native_evidence import execute

    patch = "*** Begin Patch\n*** Add File: interaction-policy.txt\n+authorized\n*** End Patch"
    for reply in (execute("printf authorized-command", "shell"),
                  execute("apply_patch <<'PATCH'\n" + patch + "\nPATCH", "patch"),
                  answer("Authorized work finished."), answer("Resumed without login.")):
        gateway.responses.put(reply)
    provider = settings(gateway, native_codex, tool_format)
    with SessionJournal(tmp_path, "interaction-policy") as journal:
        backend = create_backend(provider, registry([]), journal, CONTEXT)
        try:
            events = list(backend.run("Run the authorized project task."))
            assert events[-1]["kind"] == "task.completed"
            assert (tmp_path / "interaction-policy.txt").read_text() == "authorized\n"
            assert not backend.audit.pending and not backend.audit.index.audits
            thread_id = backend.thread_id
        finally:
            backend.close()
    with SessionJournal(tmp_path, "interaction-policy") as journal:
        backend = create_backend(provider, registry([]), journal, CONTEXT)
        try:
            assert list(backend.run("Resume inspection."))[-1]["kind"] == "task.completed"
            assert backend.thread_id == thread_id and len(gateway.requests) == 4
            assert not backend.audit.pending and not backend.audit.index.audits
        finally:
            backend.close()
