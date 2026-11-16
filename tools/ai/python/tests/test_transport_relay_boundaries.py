"""Relay wire encoding, diagnostics and one bounded request without EDA."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from sico.core.contracts import BoundContext
from sico.transport import relay as relay_module
from sico.transport.framing import VERSION, ProtocolError
from sico.transport.relay import (
    assistant_wire_arguments,
    circuit_diagnostic,
    diagnostic_sink_from_environment,
    emit_diagnostic,
    relay,
    skill_response_timeout,
)


def test_circuit_diagnostic_bounds_raw_error_and_keeps_structured_fields():
    value = circuit_diagnostic({
        "diagnostic": {
            "code": "failed",
            "raw_error": "x" * 2200,
            "function_entered": True,
            "operation_dispatched": False,
            "request_id": "r" * 200,
            "ignored": "value",
        }
    })
    assert value["code"] == "failed"
    assert value["function_entered"] is True
    assert value["operation_dispatched"] is False
    assert len(value["raw_error"]) == 2048
    assert len(value["request_id"]) == 160
    assert value["diagnostic_truncated"] is True
    assert "ignored" not in value
    assert circuit_diagnostic({}) == {}
    assert circuit_diagnostic({"diagnostic": "not-a-map"}) == {}


def test_diagnostic_sink_is_opt_in_and_writes_bounded_jsonl(tmp_path, monkeypatch):
    assert diagnostic_sink_from_environment({}) is None
    assert diagnostic_sink_from_environment({"SICO_AI_DIAGNOSTIC_LOG": "relative"}) is None
    missing = tmp_path / "missing" / "log"
    assert diagnostic_sink_from_environment(
        {"SICO_AI_DIAGNOSTIC_LOG": str(missing)}) is None

    path = tmp_path / "diagnostic.jsonl"
    sink = diagnostic_sink_from_environment({"SICO_AI_DIAGNOSTIC_LOG": str(path)})
    try:
        assert sink is not None
        emit_diagnostic(sink, "event", value="x")
        emit_diagnostic(None, "ignored")
        huge = "x" * 20000
        emit_diagnostic(sink, "event", request_id="a" * 200, method=huge,
                        diagnostic={"code": "bad", "raw_error": huge})
    finally:
        sink.close()
    lines = [json.loads(row) for row in path.read_text().splitlines()]
    assert lines[0] == {"event": "event", "value": "x"}
    assert lines[1]["diagnostic_truncated"] is True
    assert len(lines[1]["method"]) <= 160
    assert "diagnostic" in lines[1]


def test_emit_diagnostic_never_breaks_a_request_on_bad_sink():
    class Sink:
        def write(self, _value):
            raise OSError("closed")

        def flush(self):
            raise OSError("closed")

    emit_diagnostic(Sink(), "event")


@pytest.mark.parametrize("configured,expected", [
    (None, 1800.0),
    ("", 1800.0),
    ("2.5", 2.5),
    (1, 1.0),
    (3600, 3600.0),
])
def test_skill_timeout_accepts_only_bounded_values(configured, expected):
    assert skill_response_timeout({}, configured) == expected


@pytest.mark.parametrize("configured", ["bad", 0, 0.5, 3601, "nan", "inf", object()])
def test_skill_timeout_rejects_invalid_values(configured):
    with pytest.raises(ValueError, match="Invalid SKILL response timeout"):
        skill_response_timeout({}, configured)


def test_skill_timeout_environment_precedence():
    environment = {"SICO_MCP_TIMEOUT": "4", "SICO_SKILL_TIMEOUT": "8"}
    assert skill_response_timeout(environment) == 8
    assert skill_response_timeout({"SICO_MCP_TIMEOUT": "4"}) == 4


def test_assistant_arguments_are_a_single_bounded_hex_pair():
    assert assistant_wire_arguments(
        {"method": "eval_skill", "code": "abc"}) == ["eval_skill", "616263"]
    for value in (
        {},
        {"method": "eval_skill"},
        {"method": "other", "code": "abc"},
        {"method": "eval_skill", "code": ""},
        {"method": "eval_skill", "code": "x" * 65537},
    ):
        with pytest.raises(ProtocolError):
            assistant_wire_arguments(value)


class Reader:
    def __init__(self, response):
        self.closed = threading.Event()
        self.response = response
        self.calls = []

    def next(self, timeout=None):
        self.calls.append(timeout)
        self.closed.set()
        return self.response


class Connection:
    def __init__(self, _socket):
        self.socket = SimpleNamespace()
        self.sent = []
        self.received = iter((
            {"protocol": VERSION, "kind": "welcome", "generation": "g"},
            {
                "protocol": VERSION, "kind": "request",
                "method": "get_context", "instance_id": "i", "generation": "g",
                "target_id": "bound", "id": "a" * 32,
            },
        ))
        self.closed = 0

    def send(self, value):
        self.sent.append(value)

    def receive(self):
        return next(self.received)

    def close(self):
        self.closed += 1


def test_relay_handshakes_dispatches_and_forwards_one_response(monkeypatch):
    context = BoundContext("i", "g", "bound", {"capabilities": []})
    response = json.dumps({"id": "a" * 32, "ok": True, "result": {"valid": True}})
    reader = Reader(response)
    connection = Connection(None)
    output = []
    sink = SimpleNamespace(write=output.append, flush=lambda: None)
    monkeypatch.setattr(relay_module.socket, "create_connection",
                        lambda *_args, **_kwargs: object())
    monkeypatch.setattr(relay_module, "Connection", lambda _socket: connection)
    monkeypatch.setattr(relay_module.select, "select",
                        lambda *_args: ([connection.socket], [], []))
    relay("127.0.0.1", 1, "token", source=object(), sink=sink,
          reader=reader, registration={"context": context.record()},
          skill_timeout=1)
    assert connection.sent[0]["kind"] == "hello"
    assert connection.sent[0]["token"] == "token"
    assert output == ["a" * 32 + "\n"]
    assert connection.sent[-1] == {
        "protocol": VERSION, "kind": "response", "generation": "g",
        "id": "a" * 32, "ok": True, "result": {"valid": True},
    }
    assert connection.closed == 1
