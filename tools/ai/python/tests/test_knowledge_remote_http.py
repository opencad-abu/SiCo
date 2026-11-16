"""Direct Streamable HTTP contract tests without a live knowledge service."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from sico.knowledge import remote_http
from sico.knowledge.contracts import KnowledgeError


class Headers:
    def __init__(self, values=None, content_type="application/json"):
        self.values = values or {}
        self.content_type = content_type

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_content_type(self):
        return self.content_type


class Response:
    def __init__(self, payload, *, content_type="application/json", headers=None):
        self.raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.headers = Headers(headers, content_type)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self, limit=-1):
        return self.raw if limit < 0 else self.raw[:limit]


class Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def source(**extra):
    value = {"url": "https://docs.example/mcp", "timeout_sec": 5, "collection": "all"}
    value.update(extra)
    return value


def install(monkeypatch, responses):
    opener = Opener(responses)
    monkeypatch.setattr(remote_http.urllib.request, "build_opener",
                        lambda *_handlers: opener)
    return opener


def initialize():
    return Response({
        "jsonrpc": "2.0", "id": 1,
        "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
    }, headers={"Mcp-Session-Id": "session-1"})


def notification():
    return Response(b"")


def call_result(value, *, request_id=3, content_type="application/json", headers=None):
    result = {"jsonrpc": "2.0", "id": request_id, "result": value}
    if content_type == "text/event-stream":
        return Response(
            b"event: message\ndata: " + json.dumps(result).encode() + b"\n\n",
            content_type=content_type, headers=headers,
        )
    return Response(result, content_type=content_type, headers=headers)


def test_initialize_tracks_session_and_authenticates_tool_calls(monkeypatch):
    opener = install(monkeypatch, [
        initialize(), notification(),
        call_result({"content": [{"type": "text", "text": "secret body"}]}),
    ])
    client = remote_http.McpHttp(
        source(), "secret", {"HTTPS_PROXY": "http://proxy", "https_proxy": None},
        lambda: False, __import__("time").monotonic() + 10,
    )
    assert client.session == "session-1" and client.counter == 2
    first, second = opener.calls
    assert json.loads(first[0].data)["method"] == "initialize"
    assert first[0].get_header("Authorization") == "Bearer secret"
    assert not any(key.lower() == "mcp-session-id" for key in first[0].headers)
    assert json.loads(second[0].data)["method"] == "notifications/initialized"
    assert any(key.lower() == "mcp-session-id" and value == "session-1"
               for key, value in second[0].headers.items())

    assert client.tool("read", {"url": "https://docs.example/a"}) == "[credential] body"
    third = opener.calls[2][0]
    assert json.loads(third.data)["params"]["name"] == "read"
    assert third.get_header("Authorization") == "Bearer secret"


def test_sse_event_stream_and_tool_error_are_parsed(monkeypatch):
    install(monkeypatch, [
        initialize(), notification(),
        call_result({"content": [{"type": "text", "text": "sse text"}]},
                    content_type="text/event-stream"),
    ])
    client = remote_http.McpHttp(
        source(), "", {}, lambda: False, __import__("time").monotonic() + 10,
    )
    assert client.tool("read", {}) == "sse text"

    install(monkeypatch, [
        initialize(), notification(),
        call_result({"isError": True, "content": []}),
    ])
    client = remote_http.McpHttp(
        source(), "", {}, lambda: False, __import__("time").monotonic() + 10,
    )
    with pytest.raises(KnowledgeError, match="远程资料未能读取") as error:
        client.tool("read", {})
    assert error.value.code == "remote_error"


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "auth_required"), (403, "auth_required"), (500, "unavailable")],
)
def test_http_status_maps_to_stable_knowledge_errors(monkeypatch, status, code):
    error = urllib.error.HTTPError(
        "https://docs.example/mcp", status, "failure", Headers(), io.BytesIO(b""),
    )
    install(monkeypatch, [error])
    with pytest.raises(KnowledgeError) as raised:
        remote_http.McpHttp(source(), "", {}, lambda: False,
                            __import__("time").monotonic() + 10)
    assert raised.value.code == code


def test_network_cancel_timeout_and_oversize_are_bounded(monkeypatch):
    install(monkeypatch, [OSError("offline")])
    with pytest.raises(KnowledgeError) as error:
        remote_http.McpHttp(source(), "", {}, lambda: False,
                            __import__("time").monotonic() + 10)
    assert error.value.code == "unavailable"

    with pytest.raises(KnowledgeError) as error:
        remote_http.McpHttp(source(), "", {}, lambda: True,
                            __import__("time").monotonic() + 10)
    assert error.value.code == "cancelled"

    with pytest.raises(KnowledgeError) as error:
        remote_http.McpHttp(source(), "", {}, lambda: False,
                            __import__("time").monotonic() - 1)
    assert error.value.code == "timeout"

    install(monkeypatch, [Response(b"x" * (remote_http.MAX_RESPONSE + 1))])
    with pytest.raises(KnowledgeError) as error:
        remote_http.McpHttp(source(), "", {}, lambda: False,
                            __import__("time").monotonic() + 10)
    assert error.value.code == "response_limit"


@pytest.mark.parametrize(
    "response",
    [
        Response(b"not-json"),
        Response({"jsonrpc": "2.0", "id": 99, "result": {}}),
        Response({"jsonrpc": "2.0", "id": 1, "error": {"message": "bad"}}),
        Response({"jsonrpc": "2.0", "id": 1, "result": []}),
    ],
)
def test_protocol_errors_are_not_forwarded_as_results(monkeypatch, response):
    install(monkeypatch, [response])
    with pytest.raises(KnowledgeError) as error:
        remote_http.McpHttp(source(), "", {}, lambda: False,
                            __import__("time").monotonic() + 10)
    assert error.value.code == "protocol"


def test_redirect_handler_rejects_every_redirect():
    with pytest.raises(KnowledgeError) as error:
        remote_http.NoRedirect().redirect_request(
            urllib.request.Request("https://docs.example/a"), None, 302, "", {}, "https://other"
        )
    assert error.value.code == "redirect_rejected"
