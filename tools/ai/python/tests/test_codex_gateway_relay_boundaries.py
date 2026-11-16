"""Responses-to-worker frame relay and HTTP error translation."""

from __future__ import annotations

import base64
import io
import threading
from types import SimpleNamespace

import pytest
from sico.codex import gateway_relay


def row(kind, **values):
    return {"kind": kind, **values}


class Reader:
    def __init__(self, rows):
        self.rows = iter(rows)

    def next(self, _timeout, _cancelled):
        return next(self.rows)


class Connection:
    def __init__(self, rows):
        self.reader = Reader(rows)
        self.sent = []

    def send(self, value):
        self.sent.append(value)


class Handler:
    def __init__(self):
        self.replies = []
        self.status = []
        self.headers = []
        self.wfile = io.BytesIO()
        self.close_connection = False

    def reply(self, status, message, *, payload=None, request_id=""):
        self.replies.append((status, message, payload, request_id))

    def send_response(self, status):
        self.status.append(status)

    def send_header(self, name, value):
        self.headers.append((name, value))

    def end_headers(self):
        pass


def gateway():
    return SimpleNamespace(
        endpoint="http://model/v1/responses", api_key="secret",
        token="local", timeout=2, idle_timeout=3,
        http_status=None, diagnostic="")


def test_drain_end_accepts_only_data_until_end():
    reader = Reader([row("data", body="eA=="), row("end")])
    gateway_relay._drain_end(reader, 1, threading.Event())
    with pytest.raises(gateway_relay.WorkerError, match="interrupted"):
        gateway_relay._drain_end(Reader([row("error")]), 1, threading.Event())


def test_relay_forwards_chunks_headers_and_stream_data():
    connection = Connection([
        row("headers", status=200, content="application/json"),
        row("data", body=base64.b64encode(b'{"ok":true}').decode()),
        row("end"),
    ])
    handler = Handler()
    gateway_relay.relay(gateway(), connection, b"x" * 5000, handler,
                        threading.Event(), {})
    assert connection.sent[0].startswith(b"SICO-GATEWAY/1 ")
    assert any(b'"kind":"data"' in value for value in connection.sent)
    assert handler.status == [200]
    assert ("Content-Type", "application/json") in handler.headers
    assert handler.wfile.getvalue() == b'{"ok":true}'
    assert handler.close_connection is True


def test_relay_translates_upstream_error_and_redacts_body():
    connection = Connection([
        row("headers", status=400, content="application/json",
            request_id="request-1"),
        row("data", body=base64.b64encode(b"secret prompt").decode()),
        row("end"),
    ])
    handler = Handler()
    value = gateway()
    gateway_relay.relay(value, connection, b"{}", handler,
                        threading.Event(), {})
    assert handler.replies and handler.replies[0][0] == 400
    assert handler.replies[0][1] == ""
    assert value.http_status == 400
    assert "secret prompt" not in str(handler.replies[0][2])
    assert handler.replies[0][3] == "request-1"


def test_relay_rejects_worker_errors_and_invalid_headers():
    value = gateway()
    with pytest.raises(gateway_relay.UpstreamError, match="worker"):
        gateway_relay.relay(
            value, Connection([row("error", message="worker"), row("end")]), b"{}",
            Handler(), threading.Event(), {})
    with pytest.raises(gateway_relay.WorkerError, match="upstream failed"):
        gateway_relay.relay(
            value, Connection([row("headers", status="200",
                                   content="application/json")]),
            b"{}", Handler(), threading.Event(), {})


def test_relay_rejects_client_disconnect_before_dispatch():
    disconnected = threading.Event()
    disconnected.set()
    with pytest.raises(OSError, match="disconnected"):
        gateway_relay.relay(
            gateway(), Connection([row("headers", status=200,
                                       content="application/json")]),
            b"x" * 5000, Handler(), disconnected, {})
