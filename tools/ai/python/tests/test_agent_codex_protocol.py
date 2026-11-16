"""Lossless tool identities and HTTP/SSE failures at the compatibility boundary."""

from __future__ import annotations

import http.client
import io
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sico.codex.mcp import ToolBridge
from sico.codex.runtime import mcp_environment_vars
from sico.codex.settings import codex_idle_timeout, codex_tool_timeout
from sico.providers.config import load_provider

from cadai.responses_compat import unsupported_fields
from cadai.responses_sse import transform_stream
from cadai.responses_tools import CompatibilityError, ToolNames
from cadai.responses_wrapper import ResponsesWrapper


def function(name="get_context", **extra):
    return {
        "type": "function",
        "name": name,
        "description": "读取窗口",
        "parameters": {"type": "object", "properties": {"namespace": {"type": "string"}}},
        **extra,
    }


def namespace(name="mcp__sico_ic", *children):
    return {"type": "namespace", "name": name, "tools": list(children) or [function()]}


def call(**extra):
    return {
        "type": "function_call",
        "id": "fc-1",
        "call_id": "c-1",
        "arguments": "{}",
        "namespace": "mcp__sico_ic",
        "name": "get_context",
        **extra,
    }


def test_history_choices_and_payloads_round_trip_without_mutation():
    result = {
        "type": "function_call_output",
        "call_id": "c-1",
        "name": None,
        "namespace": None,
        "output": '{"namespace":"keep","name":"get_context"}',
    }
    original = {
        "model": "test",
        "tools": [namespace()],
        "input": [
            {"role": "user", "content": "namespace: mcp__sico_ic.get_context"},
            call(arguments='{"namespace":"literal"}'),
            result,
        ],
        "tool_choice": {
            "type": "allowed_tools",
            "mode": "auto",
            "tools": [
                {"type": "function", "namespace": "mcp__sico_ic", "name": "get_context"},
            ],
        },
    }
    before = json.dumps(original)
    names = ToolNames()
    outgoing = names.request(original)
    assert json.dumps(original) == before
    tool = outgoing["tools"][0]
    assert tool["name"] == "mcp__sico_ic__get_context" and tool["type"] == "function"
    assert "namespace" in tool["parameters"]["properties"]
    assert outgoing["input"][0] == original["input"][0]
    assert outgoing["input"][1]["arguments"] == original["input"][1]["arguments"]
    assert outgoing["input"][2]["output"] == result["output"]
    assert "name" not in outgoing["input"][2]
    assert outgoing["tool_choice"]["tools"][0]["name"] == tool["name"]
    restored = names.response({"output": [outgoing["input"][1]]})["output"][0]
    assert restored == original["input"][1]
    # A fresh wrapper reconstructs identical aliases from resumed native history.
    assert ToolNames().request(original) == outgoing


def test_long_names_collisions_and_custom_output_pairing():
    names = ToolNames()
    alias = names.encode("namespace" * 12, "read_中文")
    assert len(alias) <= 64 and alias == ToolNames().encode("namespace" * 12, "read_中文")
    with pytest.raises(CompatibilityError, match="collide"):
        names.request({"tools": [namespace("a", function("b")), function("a__b")]})
    with pytest.raises(CompatibilityError, match="Duplicate"):
        ToolNames().request({"tools": [function(), function()]})


def test_unknown_flat_names_pass_through_and_invalid_names_still_fail():
    # Unknown names are deliberately not decoded here: Codex owns the
    # "unsupported call" answer, so a profile-scoped session calling a
    # help-listed but unadvertised name never loses its turn.
    names = ToolNames()
    names.request({"tools": [namespace()]})
    unknown = {"type": "function_call", "id": "fc-1", "call_id": "call-1",
               "name": "not_declared", "arguments": "{}"}
    assert names.item(unknown, incoming=True) == unknown
    with pytest.raises(CompatibilityError, match="invalid flattened tool name"):
        names.item({"type": "function_call", "name": None}, incoming=True)
    with pytest.raises(CompatibilityError, match="invalid flattened tool name"):
        names.item({"type": "function_call", "name": ""}, incoming=True)
    data = ToolNames().request(
        {
            "tools": [
                namespace(
                    "customs",
                    {
                        "type": "custom",
                        "name": "parse",
                        "format": {"type": "text"},
                    },
                )
            ],
            "input": [
                {
                    "type": "custom_tool_call",
                    "namespace": "customs",
                    "name": "parse",
                    "input": "raw input",
                    "call_id": "custom-1",
                },
                {
                    "type": "custom_tool_call_output",
                    "name": "parse",
                    "output": "raw output",
                    "call_id": "custom-1",
                },
            ],
        }
    )
    assert data["tools"][0]["type"] == "custom"
    assert [i["name"] for i in data["input"]] == ["customs__parse"] * 2


def test_discovery_and_old_history_keep_reversible_identities():
    names = ToolNames()
    outgoing = names.request(
        {
            "tools": [{"type": "tool_search"}],
            "input": [
                {"type": "tool_search_output", "tools": [namespace()]},
                {"type": "additional_tools", "tools": [namespace("old", function("removed"))]},
                call(namespace="old", name="removed"),
            ],
        }
    )
    assert outgoing["tools"] == [{"type": "tool_search"}]
    assert outgoing["input"][0]["tools"][0]["type"] == "function"
    unexpected = call(name="unexpected")
    assert names.response({"output": [unexpected]})["output"][0] == unexpected
    with pytest.raises(CompatibilityError, match="discovery"):
        names.item({"type": "tool_search_output", "tools": []}, incoming=True)


def test_mcp_bridge_preserves_shared_tool_definitions():
    class Tools:
        def schemas(self):
            return [
                {
                    "name": "safe",
                    "description": "safe",
                    "input_schema": {"type": "object"},
                    "annotations": {"readOnlyHint": True, "destructiveHint": False},
                },
                {
                    "name": "write",
                    "description": "write",
                    "input_schema": {"type": "object"},
                    "annotations": {"readOnlyHint": False, "destructiveHint": True},
                },
            ]

        def mcp_schemas(self):
            return [{"name": tool["name"], "description": tool["description"],
                     "inputSchema": tool["input_schema"],
                     "annotations": tool["annotations"]}
                    for tool in self.schemas()]

    calls = []
    bridge = ToolBridge(Tools(), lambda name, args: calls.append((name, args)))
    try:
        listed = bridge.dispatch({"method": "list"})["tools"]
        assert [tool["name"] for tool in listed] == ["safe", "write"]
        bridge.dispatch({"method": "call", "name": "write", "arguments": {}})
        assert calls == [("write", {})]
    finally:
        bridge.close()


def test_codex_mcp_timeout_is_shared_and_bounded():
    assert codex_tool_timeout({}) == 1800
    assert codex_tool_timeout({"SICO_MCP_TIMEOUT": "193"}) == 193
    assert codex_tool_timeout({"SICO_AI_MCP_TIMEOUT": "310.2"}) == 311
    with pytest.raises(ValueError, match="MCP timeout"):
        codex_tool_timeout({"SICO_AI_MCP_TIMEOUT": "0"})
    with pytest.raises(ValueError, match="removed after SiCo v0.0.1"):
        codex_tool_timeout({"CAD_AI_MCP_TIMEOUT": "310.2"})
    assert codex_idle_timeout({}) == 900
    assert codex_idle_timeout({"SICO_MODEL_IDLE_TIMEOUT": "120.5"}) == 121
    with pytest.raises(ValueError, match="idle timeout"):
        codex_idle_timeout({"SICO_MODEL_IDLE_TIMEOUT": "0"})


def test_copilot_native_capability_configuration_is_explicit_and_model_key_isolated():
    codex = Path(__file__).resolve().parents[3] / "sico/python/sico" / "codex"
    runtime_source = (codex / "runtime.py").read_text(encoding="utf-8")
    assert "feature_groups" in runtime_source
    source = (codex / "feature_groups.py").read_text(encoding="utf-8")
    for marker in (
        '"features.shell_tool": True',
        '"features.shell_snapshot": True',
        '"features.plugins": False',
        '"features.apps": True',
        '"features.browser_use": True',
        '"features.computer_use": True',
        '"features.image_generation": True',
        '"features.unified_exec": True',
        '"features.multi_agent": True',
        '"features.multi_agent_v2": True',
    ):
        assert marker in source
    assert '"web_search": "live"' in source
    assert '"sandbox_mode": "workspace-write"' in source
    assert "SICO_API_KEY" not in mcp_environment_vars({})


def test_sse_preserves_order_and_complete_items_and_rejects_truncation():
    names = ToolNames()
    outgoing = names.request({"tools": [namespace()], "input": [call()]})["input"][0]
    event = {"type": "response.output_item.added", "item": outgoing}
    done = {"type": "response.completed", "response": {"output": [outgoing]}}
    raw = (
        b": heartbeat\r\nid: 7\r\nevent: response.output_item.added\r\ndata: "
        + json.dumps(event).encode()
        + b"\r\n\r\ndata: "
        + json.dumps(done).encode()
        + b"\n\ndata: [DONE]\n\n"
    )
    frames = list(transform_stream(io.BytesIO(raw), names.event))
    assert len(frames) == 3 and frames[-1] == b"data: [DONE]\n\n"
    assert frames[0].startswith(b": heartbeat\r\nid: 7\r\n")
    assert b'"namespace": "mcp__sico_ic"' in frames[0]
    assert b'"namespace": "mcp__sico_ic"' in frames[1]
    with pytest.raises(CompatibilityError, match="Truncated"):
        list(transform_stream(io.BytesIO(b'data: {"type":"test"}\n'), names.event))


class GatewayHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.server.requests.append(
            json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        )
        self.server.headers_seen.append(dict(self.headers))
        replies = getattr(self.server, "replies", None)
        if replies:
            code, content_type, body = replies.pop(0)
        else:
            code, content_type, body = self.server.reply
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Location", "http://127.0.0.1:1/never-follow")
        self.send_header("x-request-id", self.server.request_id)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def gateway():
    server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
    server.requests, server.headers_seen = [], []
    server.replies = []
    server.request_id = "request-123"
    server.reply = (200, "application/json", b'{"output":[]}')
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def post(wrapper, *, headers=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", wrapper.server.server_port, timeout=5)
    try:
        connection.request(
            "POST",
            "/v1/responses",
            json.dumps(body or {"tools": [namespace()]}),
            {"Authorization": "Bearer " + wrapper.token, **(headers or {})},
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("stream", [False, True])
def test_http_json_and_sse_are_restored_with_separate_credentials(gateway, stream):
    output = call(name="mcp__sico_ic__get_context")
    output.pop("namespace")
    response = {"output": [output]}
    body = json.dumps(response).encode()
    if stream:
        body = (
            b"data: "
            + json.dumps({"type": "response.completed", "response": response}).encode()
            + b"\n\n"
        )
    gateway.reply = (200, "text/event-stream" if stream else "application/json", body)
    with ResponsesWrapper(f"http://127.0.0.1:{gateway.server_port}", "test-real-secret") as wrapper:
        status, raw = post(wrapper)
        assert status == 200 and b'"namespace": "mcp__sico_ic"' in raw
        assert b"test-real-secret" not in raw and wrapper.token.encode() not in raw
        assert gateway.headers_seen[0]["Authorization"] == "Bearer test-real-secret"
        assert gateway.requests[0]["tools"][0]["name"] == "mcp__sico_ic__get_context"


def test_doubao_unsupported_fields_are_stripped_before_upstream(gateway):
    body = {
        "tools": [namespace()],
        "reasoning": {"summary": "auto", "effort": "low"},
        "text": {"verbosity": "low", "format": {"type": "json_schema"}},
        "input": [{"role": "user", "content": "reasoning.summary text.verbosity"}],
    }
    gateway.reply = (200, "application/json", b'{"output":[]}')
    with ResponsesWrapper(
        f"http://127.0.0.1:{gateway.server_port}", "test-real-secret",
        unsupported_fields=unsupported_fields("doubao"),
    ) as wrapper:
        status, raw = post(wrapper, body=body)
        assert status == 200 and b"test-real-secret" not in raw
        upstream = gateway.requests[0]
        assert upstream["reasoning"] == {"effort": "low"}
        assert upstream["text"] == {"format": {"type": "json_schema"}}
        assert upstream["input"] == body["input"]


@pytest.mark.parametrize("code", [302, 400, 429, 500])
def test_http_errors_do_not_retry_redirect_or_reflect_credentials(gateway, code):
    gateway.reply = (code, "application/json", b'{"error":"test-real-secret"}')
    with ResponsesWrapper(f"http://127.0.0.1:{gateway.server_port}", "test-real-secret") as wrapper:
        status, raw = post(wrapper)
        assert status == code and len(gateway.requests) == 1
        assert b"test-real-secret" not in raw and str(code).encode() in raw
        assert b"request-123" in raw
        assert post(wrapper, headers={"Authorization": "Bearer invalid"})[0] == 401
        assert len(gateway.requests) == 1


@pytest.mark.parametrize("reflected", ["upstream", "local"])
def test_error_request_id_does_not_reflect_credentials(gateway, reflected):
    gateway.reply = (401, "application/json", b'{"error":"credential rejected"}')
    with ResponsesWrapper(f"http://127.0.0.1:{gateway.server_port}", "test-real-secret") as wrapper:
        secret = wrapper.api_key if reflected == "upstream" else wrapper.token
        gateway.request_id = "request-" + secret
        status, raw = post(wrapper)
        assert status == 401
        assert secret.encode() not in raw


@pytest.mark.parametrize("kind", ["error", "response.failed", "response.incomplete"])
def test_sse_failure_diagnostics_do_not_reflect_credentials(gateway, kind):
    with ResponsesWrapper(f"http://127.0.0.1:{gateway.server_port}", "test-real-secret") as wrapper:
        error = {
            "message": wrapper.api_key,
            "code": wrapper.token,
            "param": wrapper.api_key,
            "details": {"authorization": wrapper.api_key},
        }
        event = {"type": kind, "sequence_number": 7}
        if kind == "error":
            event.update(error, error=error)
        else:
            event["response"] = {
                "id": "resp-123",
                "status": kind.split(".")[1],
                "output": [],
                "error": error,
            }
            if kind == "response.incomplete":
                event["response"]["incomplete_details"] = {"reason": wrapper.api_key}
        gateway.reply = (200, "text/event-stream", b"data: " + json.dumps(event).encode() + b"\n\n")
        status, raw = post(wrapper)
        assert status == 200
        assert wrapper.api_key.encode() not in raw and wrapper.token.encode() not in raw
        restored = json.loads(raw.removeprefix(b"data: ").strip())
        assert restored["type"] == kind and restored["sequence_number"] == 7
        if "response" in restored:
            assert restored["response"]["id"] == "resp-123"
            assert restored["response"]["status"] == kind.split(".")[1]


def test_json_error_redaction_preserves_output_and_incomplete_reason(gateway):
    response = {
        "id": "resp-123",
        "status": "incomplete",
        "error": {"message": "test-real-secret", "code": "test-real-secret"},
        "incomplete_details": {"reason": "max_output_tokens", "details": "test-real-secret"},
        "output": [{"type": "message", "id": "msg-1", "content": [{"text": "literal output"}]}],
    }
    gateway.reply = (200, "application/json", json.dumps(response).encode())
    with ResponsesWrapper(f"http://127.0.0.1:{gateway.server_port}", "test-real-secret") as wrapper:
        status, raw = post(wrapper)
        assert status == 200 and b"test-real-secret" not in raw
        restored = json.loads(raw)
        assert restored["output"] == response["output"]
        assert restored["incomplete_details"] == {"reason": "max_output_tokens"}
        assert restored["id"] == "resp-123" and restored["status"] == "incomplete"


def test_downstream_disconnect_cancels_waiting_upstream():
    # The gateway accepts the request but never sends response headers.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    entered, disconnected = threading.Event(), threading.Event()

    def stalled():
        sock, _ = listener.accept()
        with sock:
            sock.settimeout(5)
            entered.set()
            try:
                while sock.recv(65536):
                    pass
                disconnected.set()
            except OSError:
                pass

    thread = threading.Thread(target=stalled)
    thread.start()
    try:
        with ResponsesWrapper(f"http://127.0.0.1:{listener.getsockname()[1]}", "secret") as wrapper:
            client = socket.create_connection(("127.0.0.1", wrapper.server.server_port))
            body = json.dumps({"tools": [namespace()]}).encode()
            client.sendall(
                (
                    "POST /v1/responses HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer "
                    + wrapper.token
                    + "\r\nContent-Length: "
                    + str(len(body))
                    + "\r\n\r\n"
                ).encode()
                + body
            )
            assert entered.wait(3)
            client.close()
            assert disconnected.wait(3)
    finally:
        listener.close()
        thread.join(timeout=5)


def test_codex_configuration_is_explicit_and_rejects_malformed_options(tmp_path):
    env = {
        "SICO_BACKEND": "codex",
        "SICO_PROVIDER": "responses",
        "SICO_API_URL": "https://gateway.example/v1",
        "SICO_MODEL": "site-model",
        "SICO_API_KEY": "secret",
        "SICO_TOOL_FORMAT": "flat",
    }
    settings = load_provider(environment=env)
    assert settings.tool_format == "flat" and settings.endpoint.endswith("/v1/responses")
    for option in ("tool_format", "linux_sandbox", "codex_executable", "timeout"):
        config = {**settings.options, option: []}
        path = tmp_path / "provider.json"
        path.write_text(json.dumps(config))
        with pytest.raises(ValueError):
            load_provider(path, environment=env)
