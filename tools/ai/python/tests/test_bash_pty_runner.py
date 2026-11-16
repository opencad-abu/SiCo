"""PTY command-runner control, output, input and cancellation without a real PTY."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from sico.core.terminal_commands import CommandRef
from sico.core.terminal_input import TerminalInput
from sico.execution import bash_pty
from sico.execution.bash_pty import PtyCommandRunner, _Complete


class Decoder:
    def __init__(self, facts=()):
        self.facts = list(facts)
        self.cancelled_calls = []
        self.eof_calls = 0

    def feed(self, _raw):
        return list(self.facts)

    def cancelled(self, command, status):
        self.cancelled_calls.append((command, status))

    def eof(self):
        self.eof_calls += 1


class Pipes:
    sentinel_fd = 14

    def __init__(self):
        self.opened = 0
        self.released = 0
        self.closed = 0
        self.scope = False

    def observe_open(self):
        self.opened += 1

    def release_writers(self):
        self.released += 1

    def scope_ended(self, _pid):
        return self.scope

    def close(self):
        self.closed += 1


def make_runner(facts=()):
    decoder = Decoder(facts)
    pipes = Pipes()
    events = []
    shell = SimpleNamespace(pid=100, poll=lambda: None)
    runner = PtyCommandRunner(
        "cmd", shell, "namespace", 11, decoder, 12, pipes,
        lambda *args, **kwargs: events.append((args, kwargs)), 13,
    )
    return runner, decoder, pipes, events


def test_prepared_control_waits_for_matching_permit(monkeypatch):
    runner, decoder, pipes, events = make_runner()
    facts = [SimpleNamespace(kind="prepared", value=123)]
    decoder.facts = facts
    monkeypatch.setattr(bash_pty.os, "read", lambda *_args: b"prepared")
    monkeypatch.setattr(bash_pty.wire, "receive",
                        lambda *_args, **_kwargs: {"kind": "continue", "command": "cmd"})
    writes = []
    monkeypatch.setattr(bash_pty.os, "write", lambda fd, data: writes.append((fd, data)))
    runner._read_control()
    assert events[0][0] == ("prepared", "cmd")
    assert writes == [(13, b"1")]
    assert pipes.opened == 1

    monkeypatch.setattr(bash_pty.wire, "receive",
                        lambda *_args, **_kwargs: {"kind": "continue", "command": "other"})
    with pytest.raises(ValueError, match="Invalid Bash execution permit"):
        runner._read_control()


def test_control_handles_quiet_started_finished_rejected_and_invalid(monkeypatch):
    runner, decoder, pipes, events = make_runner()
    monkeypatch.setattr(bash_pty.os, "read", lambda *_args: b"x")
    decoder.facts = [SimpleNamespace(kind="started", value=1)]
    runner._read_control()
    assert runner._running is True and events[-1][0] == ("started", "cmd")

    finished = SimpleNamespace(kind="finished", value=7)
    decoder.facts = [finished]
    runner._read_control()
    assert runner._finished is finished
    decoder.facts = [SimpleNamespace(kind="rejected", value=1)]
    runner._read_control()
    assert runner._finished.kind == "rejected"
    assert pipes.opened == 3

    decoder.facts = [SimpleNamespace(kind="unknown", value=0)]
    with pytest.raises(ValueError, match="control observation"):
        runner._read_control()

    monkeypatch.setattr(bash_pty.os, "read", lambda *_args: b"")
    runner._read_control()
    assert runner._control_ended and runner._lost

    def blocked(*_args):
        raise BlockingIOError()

    runner._lost = False
    monkeypatch.setattr(bash_pty.os, "read", blocked)
    runner._read_control()
    assert runner._control_quiet is True


def test_output_is_pending_until_started_and_flushed_as_base64(monkeypatch):
    runner, _decoder, _pipes, events = make_runner()
    monkeypatch.setattr(bash_pty.os, "read", lambda *_args: b"before")
    runner._read_output()
    assert bytes(runner._pending_output) == b"before"
    runner._running = True
    runner._flush_pending()
    assert events == [(("output", "cmd"), {
        "stream": "pty", "data": base64.b64encode(b"before").decode("ascii"),
    })]

    monkeypatch.setattr(bash_pty.os, "read", lambda *_args: b"after")
    runner._read_output()
    assert bytes(runner._pending_output) == b""
    assert events[-1][1]["data"] == base64.b64encode(b"after").decode("ascii")


def test_output_overflow_and_loss_are_not_completion(monkeypatch):
    runner, _decoder, _pipes, _events = make_runner()
    monkeypatch.setattr(bash_pty.os, "read", lambda *_args: b"x" * 65537)
    with pytest.raises(ValueError, match="before command start"):
        runner._read_output()

    def failed(*_args):
        raise OSError("gone")

    monkeypatch.setattr(bash_pty.os, "read", failed)
    runner._read_output()
    assert runner._lost
    runner._lost = False
    monkeypatch.setattr(bash_pty.os, "read", lambda *_args: b"")
    runner._read_output()
    assert runner._lost


def test_sentinel_and_scope_observation(monkeypatch):
    runner, _decoder, pipes, _events = make_runner()
    monkeypatch.setattr(bash_pty.os, "read", lambda *_args: b"")
    runner._read_sentinel()
    assert runner._sentinel_eof is True
    pipes.scope = True
    runner._observe_scope()
    assert runner._scope_ended is True
    pipes.scope = False
    monkeypatch.setattr(pipes, "scope_ended",
                        lambda _pid: (_ for _ in ()).throw(OSError("gone")))
    runner._observe_scope()
    assert runner._lost


def test_relay_input_is_sequence_checked_and_delivered(monkeypatch):
    runner, _decoder, _pipes, _events = make_runner()
    runner._running = True
    ref = CommandRef("session", "terminal", "cmd")
    value = TerminalInput(ref, "user", 1, b"hello")
    delivered = []
    monkeypatch.setattr(runner, "_write_input", delivered.append)
    runner._message({"kind": "input", "input": value.record()})
    assert delivered == [b"hello"] and runner._input_sequence == 1
    with pytest.raises(ValueError, match="does not belong"):
        runner._message({"kind": "input", "input": value.record()})
    with pytest.raises(ValueError, match="Invalid relay input frame"):
        runner._message({"kind": "input"})
    with pytest.raises(ValueError, match="Invalid relay operation"):
        runner._message({"kind": "unknown"})


def test_cancel_is_bounded_and_foreground_ignores_missing_group(monkeypatch):
    runner, _decoder, _pipes, _events = make_runner()
    runner._cancel_requested = True
    runner._cancel_since = 0
    monkeypatch.setattr(bash_pty.time, "monotonic", lambda: 6)
    with pytest.raises(TimeoutError, match="never started"):
        runner._advance_cancel()

    runner._running = True
    runner._cancel_since = 5.6
    monkeypatch.setattr(bash_pty.os, "tcgetpgrp", lambda _fd: 200)
    monkeypatch.setattr(bash_pty.os, "getpgid", lambda _pid: 100)
    sent = []
    monkeypatch.setattr(bash_pty.os, "killpg", lambda group, sig: sent.append((group, sig)))
    runner._advance_cancel()
    assert sent == [(200, bash_pty.signal.SIGINT)]
    assert runner._cancel_signal_sent and runner._cancel_deadline == 11

    runner._cancel_deadline = 1
    runner._sentinel_eof = True
    runner._scope_ended = True
    monkeypatch.setattr(bash_pty.os, "tcgetpgrp", lambda _fd: 100)
    monkeypatch.setattr(runner._decoder, "cancelled", runner._decoder.cancelled)
    monkeypatch.setattr(bash_pty.os, "read", lambda *_args: b"")
    with pytest.raises(_Complete):
        runner._advance_cancel()


def test_normal_completion_rejected_and_loss_paths(monkeypatch):
    runner, _decoder, pipes, events = make_runner()
    runner._finished = SimpleNamespace(kind="finished", value=0)
    runner._scope_ended = True
    runner._sentinel_eof = True
    monkeypatch.setattr(bash_pty.os, "read", lambda *_args: b"")
    assert runner._normal_complete() is True
    assert events[-1][0] == ("complete", "cmd")
    assert pipes.released == 0

    runner, _decoder, pipes, events = make_runner()
    runner._finished = SimpleNamespace(kind="rejected", value=1)
    runner._scope_ended = True
    runner._sentinel_eof = True
    assert runner._normal_complete() is True
    assert events[-1][0] == ("failed", "cmd")


def test_failure_and_loss_flush_pending_output(monkeypatch):
    runner, decoder, pipes, events = make_runner()
    runner._pending_output.extend(b"tail")
    runner._lost = True
    runner._running = True
    monkeypatch.setattr(bash_pty.os, "read",
                        lambda *_args: (_ for _ in ()).throw(BlockingIOError()))
    runner._advance_loss()
    assert pipes.released == 1 and events[-1][1]["data"] == base64.b64encode(b"tail").decode()
    runner._fail()
    assert decoder.eof_calls == 1 and events[-1][0] == ("failed", "cmd")


def test_run_returns_on_completion_exception_and_loss(monkeypatch):
    runner, _decoder, pipes, _events = make_runner()
    monkeypatch.setattr(bash_pty.select, "select", lambda *_args: ([], [], [], []))
    monkeypatch.setattr(bash_pty.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner, "_flush_pending", lambda: None)
    monkeypatch.setattr(runner, "_observe_scope", lambda: None)
    monkeypatch.setattr(runner, "_normal_complete", lambda: True)
    assert runner.run() is True
    assert pipes.closed == 1

    runner, _decoder, pipes, _events = make_runner()
    monkeypatch.setattr(runner, "_flush_pending", lambda: None)
    monkeypatch.setattr(runner, "_observe_scope", lambda: None)
    monkeypatch.setattr(runner, "_normal_complete",
                        lambda: (_ for _ in ()).throw(_Complete()))
    assert runner.run() is True
    assert pipes.closed == 1

    runner, _decoder, pipes, _events = make_runner()
    runner._lost = True
    runner._loss_deadline = 0
    failed = []
    monkeypatch.setattr(runner, "_flush_pending", lambda: None)
    monkeypatch.setattr(runner, "_observe_scope", lambda: None)
    monkeypatch.setattr(runner, "_normal_complete", lambda: False)
    monkeypatch.setattr(runner, "_advance_loss", lambda: None)
    monkeypatch.setattr(runner, "_fail", lambda: failed.append(True))
    assert runner.run() is False
    assert failed == [True] and pipes.closed == 1


def test_sentinel_and_message_wire_errors(monkeypatch):
    runner, _decoder, _pipes, _events = make_runner()
    monkeypatch.setattr(bash_pty.os, "read", lambda *_args: b"unexpected")
    with pytest.raises(ValueError, match="sentinel bytes"):
        runner._read_sentinel()

    def blocked(*_args):
        raise BlockingIOError()

    monkeypatch.setattr(bash_pty.os, "read", blocked)
    runner._read_sentinel()
    runner._read_messages()
    monkeypatch.setattr(bash_pty.os, "read", lambda *_args: b"")
    with pytest.raises(EOFError, match="Relay input closed"):
        runner._read_messages()

    runner, _decoder, _pipes, _events = make_runner()
    runner._incoming = SimpleNamespace(feed=lambda _raw: [{"kind": "cancel", "command": "cmd"}])
    monkeypatch.setattr(bash_pty.os, "read", lambda *_args: b"frame")
    runner._read_messages()
    assert runner._cancel_requested is True


def test_write_input_retries_blocking_and_rejects_timeout_and_close(monkeypatch):
    runner, _decoder, _pipes, _events = make_runner()
    monkeypatch.setattr(bash_pty.select, "select", lambda *_args: ([], [12], []))
    writes = iter([BlockingIOError(), 2, 1])

    def write(_fd, view):
        value = next(writes)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(bash_pty.os, "write", write)
    runner._write_input(b"abc")

    monkeypatch.setattr(bash_pty.select, "select", lambda *_args: ([], [], []))
    with pytest.raises(TimeoutError, match="could not be delivered"):
        runner._write_input(b"x")
    monkeypatch.setattr(bash_pty.select, "select", lambda *_args: ([], [12], []))
    monkeypatch.setattr(bash_pty.os, "write", lambda *_args: 0)
    with pytest.raises(BrokenPipeError, match="PTY input closed"):
        runner._write_input(b"x")


def test_cancel_early_returns_and_missing_foreground_group(monkeypatch):
    runner, _decoder, _pipes, _events = make_runner()
    runner._finished = SimpleNamespace(kind="finished", value=0)
    runner._advance_cancel()
    runner._finished = None
    runner._lost = True
    runner._advance_cancel()
    runner._lost = False
    runner._running = True
    runner._cancel_since = 10
    runner._cancel_signal_sent = True
    runner._cancel_deadline = 100
    monkeypatch.setattr(bash_pty.os, "tcgetpgrp", lambda _fd: 100)
    monkeypatch.setattr(bash_pty.os, "getpgid", lambda _pid: 100)
    monkeypatch.setattr(bash_pty.time, "monotonic", lambda: 10)
    runner._advance_cancel()

    monkeypatch.setattr(bash_pty.os, "tcgetpgrp",
                        lambda _fd: (_ for _ in ()).throw(OSError("gone")))
    runner._cancel_signal_sent = False
    runner._advance_cancel()
    assert runner._lost is True


def test_drain_and_fail_tolerate_closed_descriptor(monkeypatch):
    runner, decoder, _pipes, events = make_runner()
    reads = iter([b"tail", b""])
    monkeypatch.setattr(bash_pty.os, "read", lambda *_args: next(reads))
    runner._drain_output()
    assert events[-1][1]["data"] == base64.b64encode(b"tail").decode()
    monkeypatch.setattr(bash_pty.os, "read",
                        lambda *_args: (_ for _ in ()).throw(BlockingIOError()))
    runner._drain_output()
    monkeypatch.setattr(decoder, "eof",
                        lambda: (_ for _ in ()).throw(ValueError("closed")))
    runner._fail()
    assert events[-1][0] == ("failed", "cmd")
