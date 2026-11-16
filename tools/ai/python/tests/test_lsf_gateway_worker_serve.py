"""Gateway worker control loop and error envelopes."""

from __future__ import annotations

import io
import os
from types import SimpleNamespace

import pytest

from sico.codex import gateway_worker
from sico.codex.gateway_wire import PREFIX, chunk, encode


class Buffer:
    def __init__(self):
        self.data = bytearray()

    def write(self, value):
        self.data.extend(value)
        return len(value)

    def flush(self):
        pass


def frames(output):
    prefix = PREFIX.decode()
    return [line[len(prefix):] for line in output.data.decode().splitlines()
            if line.startswith(prefix)]


def serve_frames(monkeypatch, payload, validate=None):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, payload)
    os.close(write_fd)
    source = os.fdopen(read_fd, "rb", buffering=0)
    output = Buffer()
    monkeypatch.setattr(gateway_worker.sys, "stdin", SimpleNamespace(buffer=source))
    monkeypatch.setattr(gateway_worker.sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(gateway_worker.os, "isatty", lambda _fd: False)
    monkeypatch.setattr("sicoruntime.validate",
                        validate or (lambda _environment: None))
    try:
        result = gateway_worker.serve()
    finally:
        source.close()
    return result, output


def test_serve_ready_shutdown_and_validation_error(monkeypatch):
    result, output = serve_frames(monkeypatch, encode("shutdown") + b"\n")
    assert result is None
    assert frames(output)[0].startswith('{"kind":"ready"')

    def reject(_environment):
        raise RuntimeError("bad runtime")

    result, output = serve_frames(monkeypatch, b"", validate=reject)
    assert result is None
    assert "bad runtime" in frames(output)[0]


def test_serve_request_body_exchange_and_shutdown(monkeypatch):
    seen = []

    def exchange(config, body, reader, output):
        seen.append((config, body))
        return False

    monkeypatch.setattr(gateway_worker, "_exchange", exchange)
    request = encode("request", endpoint="http://127.0.0.1:1/v1/responses",
                     key="secret", timeout=3, idle=3600)
    payload = request + chunk(b"{}") + encode("end") + encode("shutdown") + b"\n"
    result, output = serve_frames(monkeypatch, payload)
    assert result is None
    assert seen[0][1] == b"{}"
    assert [row.split(":", 1)[0] for row in frames(output)][0] == '{"kind"'
    assert '"kind":"ready"' in output.data.decode()


def test_serve_rejects_invalid_control_and_body(monkeypatch):
    _result, output = serve_frames(monkeypatch, encode("other") + b"\n")
    text = output.data.decode()
    assert '"Invalid gateway control frame"' in text
    _result, output = serve_frames(monkeypatch, encode("request") + encode("bad") + b"\n")
    text = output.data.decode()
    assert '"Invalid gateway request body"' in text


def test_main_maps_runtime_failure_without_exposing_untrusted_text(monkeypatch):
    monkeypatch.setattr(gateway_worker, "serve",
                        lambda: (_ for _ in ()).throw(KeyError("secret-value")))
    assert gateway_worker.main() == 2
