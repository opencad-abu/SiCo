"""Bash relay framing and ownership boundaries without a real managed shell."""

from __future__ import annotations

import tempfile
from types import SimpleNamespace

import pytest
from sico.core.terminal_commands import CommandRef, CommandRequest
from sico.execution import bash_relay
from sico.execution.bash_relay import BashRelay


class Decoder:
    def __init__(self):
        self.last_status = None
        self.commands = []

    def expect(self, command):
        self.commands.append(command)


class Pipes:
    def __init__(self, _request):
        self.closed = 0

    def dispatch(self, _fd, **_kwargs):
        return b"echo hi\n"

    def close(self):
        self.closed += 1


def relay(mode="pipes"):
    value = BashRelay.__new__(BashRelay)
    value.mode = mode
    value.shell = SimpleNamespace(
        stdin=tempfile.TemporaryFile(),
        wait=lambda timeout=None: 0,
        pid=10,
    )
    value.control_fd = 10
    value.continue_fd = 11
    value.pty_fd = 12
    value.control_read = 13
    value.continue_write = 14
    value.pty_master = 15
    value.namespace = "namespace"
    value.decoder = Decoder()
    value.emitted = []
    value.emit = lambda kind, command=None, **values: value.emitted.append(
        (kind, command, values))
    return value


def request():
    return CommandRequest(CommandRef("session", "terminal", "command"),
                          "request", None, "env", mode="pipes",
                          shell="/bin/bash", text="echo hi")


def test_run_pipes_writes_input_and_closes_command_pipes(monkeypatch):
    value = relay()
    value._drain = lambda command, pipes: ("drained", command, pipes)
    monkeypatch.setattr(bash_relay, "CommandPipes", Pipes)
    result = value.run(request())
    assert result[0] == "drained"
    assert value.decoder.commands == ["command"]
    assert result[2].closed == 1


def test_run_pty_delegates_to_runner(monkeypatch):
    value = relay("pty")

    class Runner:
        def __init__(self, *args):
            self.args = args

        def run(self):
            return "pty-result"

    monkeypatch.setattr(bash_relay, "CommandPipes", Pipes)
    monkeypatch.setattr(bash_relay, "PtyCommandRunner", Runner)
    assert value.run(request()) == "pty-result"


def test_run_closes_pipes_when_dispatch_fails(monkeypatch):
    value = relay()

    class Broken(Pipes):
        def dispatch(self, _fd, **_kwargs):
            raise RuntimeError("dispatch failed")

    monkeypatch.setattr(bash_relay, "CommandPipes", Broken)
    with pytest.raises(RuntimeError, match="dispatch"):
        value.run(request())


def test_run_rejects_a_closed_shell_input(monkeypatch):
    value = relay()
    value.shell.stdin = SimpleNamespace(fileno=lambda: 999999)
    value._drain = lambda *_args: "drained"
    monkeypatch.setattr(bash_relay, "CommandPipes", Pipes)
    with pytest.raises(OSError):
        value.run(request())


def test_emit_uses_the_wire_encoder(monkeypatch):
    sent = []
    value = BashRelay.__new__(BashRelay)
    monkeypatch.setattr(bash_relay.wire, "send", lambda fd, row: sent.append((fd, row)))
    BashRelay.emit(value, "output", "command", stream="stdout", data="x")
    assert sent == [(1, {"kind": "output", "command": "command",
                         "stream": "stdout", "data": "x"})]


def test_main_runs_until_eof_and_maps_failure(monkeypatch):
    messages = [
        {"kind": "initialize", "environment": {"PATH": "/bin"}},
        {"kind": "execute", "request": request().record()},
    ]
    class Relay:
        def __init__(self, environment, mode):
            self.environment, self.mode = environment, mode
            self.closed = 0
            self.shell = SimpleNamespace(pid=10)
            self.namespace = "namespace"

        def emit(self, _kind, **_values):
            pass

        def run(self, _request):
            return True

        def close(self):
            self.closed += 1

    def receive(_fd, timeout=None):
        if messages:
            return messages.pop(0)
        raise EOFError

    monkeypatch.setattr(bash_relay, "BashRelay", Relay)
    monkeypatch.setattr(bash_relay.wire, "receive", receive)
    assert bash_relay.main() == 0

    messages[:] = [
        {"kind": "initialize", "environment": {"PATH": "/bin"}},
        {"kind": "execute", "request": request().record()},
    ]
    class Failed(Relay):
        def run(self, _request):
            return False
    monkeypatch.setattr(bash_relay, "BashRelay", Failed)
    assert bash_relay.main() == 1


def test_main_rejects_invalid_initialization(monkeypatch):
    monkeypatch.setattr(bash_relay.wire, "receive",
                        lambda *_args, **_kwargs: {"kind": "bad"})
    with pytest.raises(ValueError):
        bash_relay.main()


def test_close_closes_all_owned_descriptors(monkeypatch):
    value = relay()
    closed = []
    value.shell.stdin = SimpleNamespace(close=lambda: closed.append("stdin"))
    value.control_read = 20
    value.continue_write = 21
    value.pty_master = 22
    monkeypatch.setattr(bash_relay.os, "close", closed.append)
    value.close()
    assert closed == ["stdin", 20, 21, 22]


def test_drain_completes_after_started_and_closed_streams(monkeypatch):
    value = relay()
    facts = [
        SimpleNamespace(kind="started", command_id="command", value=None),
        SimpleNamespace(kind="finished", command_id="command", value=0),
    ]
    pipes = SimpleNamespace(
        observe_open=lambda: None,
        release_writers=lambda: None,
        scope_ended=lambda _pid: True,
        read=lambda _stream: b"",
    )
    monkeypatch.setattr(bash_relay.os, "read", lambda *_args: b"fact")
    value.decoder.feed = lambda _raw: facts
    value.decoder.eof = lambda: None
    assert value._drain("command", pipes) is True
    assert ("complete", "command", {"exit_code": 0}) in value.emitted


def test_drain_reports_lost_boundary_without_hanging(monkeypatch):
    value = relay()
    pipes = SimpleNamespace(
        observe_open=lambda: None,
        release_writers=lambda: None,
        scope_ended=lambda _pid: True,
        read=lambda _stream: b"",
    )
    monkeypatch.setattr(bash_relay.os, "read", lambda *_args: b"")
    value.decoder.feed = lambda _raw: ()
    value.decoder.eof = lambda: None
    assert value._drain("command", pipes) is False
    assert ("failed", "command", {}) in value.emitted


def test_bash_relay_rejects_unknown_mode():
    with pytest.raises(ValueError, match="Unsupported Bash relay mode"):
        bash_relay.BashRelay({}, "unknown")
