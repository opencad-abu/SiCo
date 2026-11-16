"""Isolated native checks, explicit calls, authentication and cancellation."""

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from sico.codex.resource_probe import check
from sico.codex.rpc import RpcError
from test_agent_codex_native import native_codex  # noqa: F401
from test_agent_settings_native import server
from test_agent_settings_service import client
from test_codex_resources_runtime import make_resources


def probe_settings():
    return SimpleNamespace(options={}, environment={"PATH": os.environ["PATH"],
        "LD_LIBRARY_PATH": "/software/pkgs/python/3.9.13/lib",
        "EXTERNAL_TOKEN": "external-secret-sentinel", "SICO_API_KEY": "model-secret"})


def test_probe_lists_and_explicitly_calls_without_model_or_default_write(tmp_path, native_codex):  # noqa: F811
    _manifest, value = make_resources(tmp_path)
    value = {"version": 2, "mcp_servers": [server(tmp_path)]}
    result = check(tmp_path, value, probe_settings(), native_codex, lambda: False)
    assert result["mcp_servers"][0]["status"] == "available"
    assert result["call_result"] is None
    result = check(tmp_path, value, probe_settings(), native_codex, lambda: False,
                   {"server": "external", "tool": "proof", "arguments": {}})
    assert "EXTERNAL_MCP_PROOF" in result["call_result"]
    assert not (tmp_path / ".sico").exists()
    assert "external-secret-sentinel" not in json.dumps(result)


def test_probe_cancellation_reaps_stalled_server(tmp_path, native_codex):  # noqa: F811
    row = server(tmp_path)
    pid_file = tmp_path / "pid"
    script = tmp_path / "hang.py"
    script.write_text("import os,time\nfrom pathlib import Path\n"
                      "Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid()))\n"
                      "time.sleep(100)\n")
    row.update(args=[str(script)], startup_timeout_sec=30)
    stop, errors = threading.Event(), []
    def run():
        try:
            check(tmp_path, {"version": 2, "mcp_servers": [row]}, probe_settings(),
                  native_codex, stop.is_set)
        except (ValueError, RpcError) as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        wait_until(pid_file.exists)
        pid = int(pid_file.read_text())
        stop.set()
        thread.join(5)
        assert not thread.is_alive() and errors
        assert not Path("/proc/" + str(pid)).exists()
    finally:
        stop.set()
        thread.join(5)


@pytest.mark.parametrize("auth", [True, False])
def test_streamable_http_bearer_auth_and_explicit_call(tmp_path, native_codex, auth):  # noqa: F811
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            if self.headers.get("Authorization") != "Bearer external-secret-sentinel":
                self.send_response(401)
                self.end_headers()
                return
            message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(message["method"])
            if "id" not in message:
                self.send_response(202)
                self.end_headers()
                return
            if message["method"] == "initialize":
                result = {"protocolVersion": message["params"]["protocolVersion"],
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "http", "version": "1"}}
            elif message["method"] == "tools/list":
                result = {"tools": [{"name": "read", "description": "Read test evidence.",
                                    "inputSchema": {"type": "object"}}]}
            else:
                result = {"content": [{"type": "text", "text": "HTTP_PROOF"}]}
            raw = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever)
    thread.start()
    try:
        row = dict(id="remote", name="Remote", source="project", enabled=True,
                   transport="streamable_http",
                   url="http://127.0.0.1:" + str(http.server_port) + "/mcp",
                   credential_ref="EXTERNAL_TOKEN", startup_timeout_sec=2)
        settings = probe_settings()
        if not auth:
            settings.environment["EXTERNAL_TOKEN"] = "expired-token"
        value = check(tmp_path, {"version": 2, "mcp_servers": [row]}, settings,
                      native_codex, lambda: False,
                      {"server": "remote", "tool": "read", "arguments": {}} if auth else None)
        assert value["mcp_servers"][0]["status"] == ("available" if auth else "unavailable")
        assert ("tools/call" in calls) == auth
        assert "HTTP_PROOF" in (value["call_result"] or "") if auth else True
    finally:
        http.shutdown()
        http.server_close()
        thread.join(5)


def test_authenticated_service_probe_cancel_is_scoped(tmp_path, monkeypatch, native_codex):  # noqa: F811
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        service.owner._default_environment = probe_settings().environment
        service.owner._default_environment["SICO_CODEX_CLI"] = native_codex
        value = {"version": 2, "mcp_servers": [server(tmp_path)]}
        result = api.agent_settings.check("check-one", value).result(10)
        assert result["status"] == "checked", result
        api.agent_settings.cancel_check("check-two").result(5)
        result = api.agent_settings.check("check-two", value).result(5)
        assert result["status"] == "cancelled"
        assert not service.owner.controllers and not service.provider.seen


def test_real_service_cancel_during_discovery_and_other_client_cannot_cancel(
        tmp_path, monkeypatch, native_codex):  # noqa: F811
    row = server(tmp_path, "my-service.v1")
    pid_file = tmp_path / "service-pid"
    script = tmp_path / "slow.py"
    script.write_text("import os,time\nfrom pathlib import Path\n"
                      "Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid()))\n"
                      "time.sleep(100)\n")
    row.update(args=[str(script)], startup_timeout_sec=30)
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        service.owner._default_environment = probe_settings().environment
        service.owner._default_environment["SICO_CODEX_CLI"] = native_codex
        with client(service, tmp_path) as other:
            other.agent_settings.cancel_check("live-check").result(5)
            future = api.agent_settings.check("live-check", {"version": 2, "mcp_servers": [row]})
            wait_until(pid_file.exists)
            assert not future.done()
            api.agent_settings.cancel_check("live-check").result(5)
            result = future.result(5)
            assert result["status"] in {"cancelled", "failed"} and "取消" in result["message"]
            assert not Path("/proc/" + pid_file.read_text()).exists()
        assert not service.owner.controllers and not service.provider.seen


def test_native_skill_preview_dependency_missing_and_unknown_override(tmp_path, native_codex):  # noqa: F811
    _manifest, value = make_resources(tmp_path)
    from sico.agent_resources.schema import upgrade_manifest

    value = upgrade_manifest(value)
    value["capability_roots"] = []
    path = tmp_path / "skills/b-user-proof/SKILL.md"
    result = check(tmp_path, value, probe_settings(), native_codex, lambda: False,
                   skill={"name": "b-user-proof", "path": str(path)})
    assert "B_RESOURCE_PROOF" in result["skill_preview"]
    value["skill_overrides"] = [{"path": str(tmp_path / "absent/SKILL.md"), "enabled": False}]
    with pytest.raises(ValueError, match="不在本会话发现目录"):
        check(tmp_path, value, probe_settings(), native_codex, lambda: False)


@pytest.mark.parametrize("required", [False, True])
def test_native_stdio_startup_timeout_and_required_failure(tmp_path, native_codex, required):  # noqa: F811
    row = server(tmp_path, "my-service.v1")
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(100)\n")
    row.update(args=[str(script)], startup_timeout_sec=0.1, required=required)
    if required:
        with pytest.raises(RpcError):
            check(tmp_path, {"version": 2, "mcp_servers": [row]}, probe_settings(),
                  native_codex, lambda: False)
    else:
        result = check(tmp_path, {"version": 2, "mcp_servers": [row]}, probe_settings(),
                       native_codex, lambda: False)
        assert result["mcp_servers"][0]["status"] == "unavailable"


def test_explicit_call_timeout_has_no_automatic_retry(tmp_path, native_codex):  # noqa: F811
    row = server(tmp_path)
    script = Path(row["args"][0])
    called = tmp_path / "called"
    script.write_text(script.read_text().replace("elif m['method']=='tools/call':",
        "elif m['method']=='tools/call':\n  open(" + repr(str(called)) +
        ", 'a').write('called\\n')\n  __import__('time').sleep(100)"))
    row["tool_timeout_sec"] = 0.1
    with pytest.raises(RpcError):
        check(tmp_path, {"version": 2, "mcp_servers": [row]}, probe_settings(),
              native_codex, lambda: False,
              {"server": "external", "tool": "proof", "arguments": {}})
    assert called.read_text() == "called\n"
