"""Direct gateway worker framing and upstream exchange contracts."""

from __future__ import annotations

import base64
import io
import json
import time

import pytest

from sico.codex import gateway_worker
from sico.codex.gateway_wire import PREFIX


class Reader:
    def __init__(self, rows=(), *, timeout=False):
        self.rows = list(rows)
        self.timeout = timeout

    def next(self, _timeout, _cancelled=None):
        if self.timeout:
            time.sleep(.001)
            raise TimeoutError()
        return self.rows.pop(0)


def test_request_validates_endpoint_key_timeout_and_idle(monkeypatch):
    monkeypatch.setattr(gateway_worker, "validate_key", lambda value: value)
    config = dict(endpoint="http://127.0.0.1:1234/v1/responses", key="secret",
                  timeout=5, idle=3600)
    url, key, timeout, idle = gateway_worker._request(config)
    assert (url.scheme, url.hostname, url.port, key, timeout, idle) == (
        "http", "127.0.0.1", 1234, "secret", 5, 3600,
    )
    for value in (0, -1, 121, "5"):
        with pytest.raises(ValueError, match="Invalid gateway timeout"):
            gateway_worker._request({**config, "timeout": value})
    with pytest.raises(ValueError):
        gateway_worker._request({**config, "idle": 0})


def test_read_body_accepts_bounded_data_and_rejects_bad_or_large_frames(monkeypatch):
    def frame(data):
        return {"kind": "data",
                "body": base64.b64encode(data).decode("ascii")}

    assert gateway_worker._read_body(Reader([frame(b"hello"), {"kind": "end"}])) == b"hello"
    with pytest.raises(ValueError, match="Invalid gateway request body"):
        gateway_worker._read_body(Reader([{"kind": "bad"}]))
    monkeypatch.setattr(gateway_worker, "MAX_BODY", 4)
    with pytest.raises(ValueError, match="exceeds limit"):
        gateway_worker._read_body(Reader([frame(b"hello"), {"kind": "end"}]))


class Socket:
    def __init__(self):
        self.timeouts = []
        self.shutdowns = []

    def settimeout(self, value):
        self.timeouts.append(value)

    def shutdown(self, _how):
        self.shutdowns.append(True)


class Response:
    status = 200

    def __init__(self, *, content_type="application/json", payload=b"ok"):
        self.content_type = content_type
        self.payload = payload

    def getheader(self, name, default=None):
        if name == "Content-Type":
            return self.content_type
        return default

    def read1(self, _size):
        value, self.payload = self.payload, b""
        return value


class Connection:
    instances = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock = Socket()
        self.requests = []
        self.response = Response()
        type(self).instances.append(self)

    def connect(self):
        self.connected = True

    def request(self, method, path, *, body, headers):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def decode_frames(raw):
    prefix = PREFIX.decode()
    return [json.loads(line[len(prefix):]) for line in raw.decode().splitlines()
            if line.startswith(prefix)]


def test_exchange_sends_headers_body_and_bounded_output(monkeypatch):
    monkeypatch.setattr(gateway_worker.http.client, "HTTPConnection", Connection)
    output = io.BytesIO()
    config = dict(endpoint="http://127.0.0.1:80/v1/responses", key="secret",
                  timeout=3, idle=2)
    Connection.instances.clear()
    assert gateway_worker._exchange(
        config, b'{"input":"x"}', Reader(timeout=True), output,
    ) is False
    frames = decode_frames(output.getvalue())
    assert [row["kind"] for row in frames] == ["headers", "data", "end"]
    connection = Connection.instances[-1]
    assert connection.requests[0][0:2] == ("POST", "/v1/responses")
    assert connection.requests[0][3]["Authorization"] == "Bearer secret"
    assert connection.requests[0][3]["Accept-Encoding"] == "identity"
    assert connection.sock.timeouts == [2] and connection.closed


def test_exchange_rejects_unsupported_content_and_maps_transport_failure(monkeypatch):
    class Bad(Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.response = Response(content_type="text/plain")

    monkeypatch.setattr(gateway_worker.http.client, "HTTPConnection", Bad)
    output = io.BytesIO()
    assert gateway_worker._exchange(
        dict(endpoint="http://127.0.0.1:1/v1/responses", key="k", timeout=3, idle=2),
        b"{}", Reader(timeout=True), output,
    ) is False
    assert "error" in [row["kind"] for row in decode_frames(output.getvalue())]

    class Failing(Connection):
        def connect(self):
            raise OSError("offline")

    monkeypatch.setattr(gateway_worker.http.client, "HTTPConnection", Failing)
    output = io.BytesIO()
    assert gateway_worker._exchange(
        dict(endpoint="http://127.0.0.1:1/v1/responses", key="k", timeout=3, idle=2),
        b"{}", Reader(timeout=True), output,
    ) is False
    assert [row["kind"] for row in decode_frames(output.getvalue())] == ["error", "end"]
