"""Responses wrapper transport, compatibility and upstream error boundaries."""

from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, request

from cadai.responses_wrapper import ResponsesWrapper


class Upstream:
    reply = (200, "application/json", b'{"output":[]}')
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            Upstream.seen.append((self.path, dict(self.headers), body))
            status, content_type, payload = Upstream.reply
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/responses"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


@contextmanager
def wrapper(url="http://127.0.0.1:1/v1"):
    value = ResponsesWrapper(url, "private-key", timeout=3)
    try:
        yield value
    finally:
        value.close()


def post(value, path="/responses", body=b'{"input":[]}', headers=None):
    values = {"Authorization": "Bearer " + value.token}
    values.update(headers or {})
    try:
        response = request.urlopen(request.Request(
            value.base_url + path, body, headers=values), timeout=5)
    except error.HTTPError as exc:
        response = exc
    with response:
        return response.status, response.read()


def raw_post(value, headers, path="/v1/responses"):
    connection = socket.create_connection(
        ("127.0.0.1", value.server.server_port), timeout=3)
    try:
        lines = [f"POST {path} HTTP/1.1", "Host: localhost", *headers,
                 "Connection: close"]
        connection.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        return connection.recv(4096).split(b"\r\n", 1)[0]
    finally:
        connection.close()


def test_response_and_event_sanitize_provider_diagnostics():
    with wrapper() as value:
        assert value.request_id("request-1") == "request-1"
        assert value.request_id("private-key") == ""
        response = value.response({
            "error": {"message": "private diagnostics"},
            "incomplete_details": {"reason": "other"},
        })
        assert response["error"] == {
            "code": "server_error",
            "message": "Model gateway reported a Responses error",
        }
        assert response["incomplete_details"] == {"reason": "unknown"}
        event = value.event({"type": "error", "sequence_number": 3,
                             "error": {"message": "private"}})
        assert event["code"] == "server_error"
        assert event["sequence_number"] == 3
        nested = value.event({"type": "response.completed",
                              "response": {"error": {"message": "private"}}})
        assert nested["response"]["error"]["code"] == "server_error"


def test_wrapper_requires_auth_endpoint_and_bounded_request():
    with wrapper() as value:
        assert post(value, headers={"Authorization": "bad"})[0] == 401
        assert post(value, path="/other")[0] == 404
        for headers in (
            ["Authorization: Bearer " + value.token, "Content-Length: 0"],
            ["Authorization: Bearer " + value.token, "Content-Length: 1",
             "Transfer-Encoding: chunked"],
            ["Authorization: Bearer " + value.token, "Content-Length: 1",
             "Content-Encoding: gzip"],
        ):
            assert b"502" in raw_post(value, headers)


def test_wrapper_forwards_json_and_redacts_upstream_errors():
    upstream = Upstream()
    Upstream.reply = (200, "application/json", b'{"output":[]}')
    try:
        with wrapper(upstream.url) as value:
            status, body = post(value)
            assert status == 200 and json.loads(body) == {"output": []}
            assert Upstream.seen[-1][0] == "/v1/responses"
            assert Upstream.seen[-1][1]["Authorization"] == "Bearer private-key"
        Upstream.reply = (429, "application/json", json.dumps({
            "error": {"code": "rate_limit", "message": "private-key secret"},
        }).encode())
        with wrapper(upstream.url) as value:
            status, body = post(value)
            assert status == 429
            assert b"private-key" not in body and b"secret" not in body
            assert value.http_status == 429
    finally:
        upstream.close()


def test_wrapper_streams_sse_and_rejects_unsupported_upstream_shapes():
    upstream = Upstream()
    Upstream.reply = (200, "text/event-stream",
                      b'data: {"type":"response.completed","response":{"output":[]}}\n\n')
    try:
        with wrapper(upstream.url) as value:
            status, body = post(value)
            assert status == 200 and b"response.completed" in body
        Upstream.reply = (200, "text/plain", b"bad")
        with wrapper(upstream.url) as value:
            assert post(value)[0] == 502
            assert "JSON or SSE" in value.diagnostic
    finally:
        upstream.close()


def test_wrapper_rejects_compressed_upstream_response():
    upstream = Upstream()
    Upstream.reply = (200, "application/json", b"{}")
    try:
        with wrapper(upstream.url) as value:
            value.connection = lambda: _CompressedConnection()
            assert post(value)[0] == 502
            assert "Compressed" in value.diagnostic
    finally:
        upstream.close()


class _CompressedConnection:
    class Response:
        status = 200

        def getheader(self, name, default=""):
            return "gzip" if name == "Content-Encoding" else default

        def read(self, _size):
            return b""

        def close(self):
            pass

    def __init__(self):
        self.sock = _Socket()

    def connect(self):
        pass

    def close(self):
        pass

    def request(self, *_args, **_kwargs):
        pass

    def getresponse(self):
        return self.Response()


class _Socket:
    def settimeout(self, _value):
        pass

    def shutdown(self, _value):
        pass
