"""Managed Bash relay boundaries without starting a real managed process."""

from __future__ import annotations

import base64
import threading
import time
from collections import deque
from types import SimpleNamespace

import pytest
from sico.core.terminal_commands import CommandRef, CommandRequest
from sico.core.terminal_session import TerminalAttempt, TerminalGeneration, TerminalRef
from sico.execution import relay_wire as wire
from sico.execution.managed_bash import ManagedBashBackend


class Process:
    def __init__(self):
        self.writes = []
        self.reads = []
        self.observed = []
        self.started = []
        self.closed = 0
        self.terminated = 0

    def start(self, argv, environment):
        self.started.append((argv, environment))

    def write(self, value):
        self.writes.append(value)

    def read(self, timeout=None):
        return self.reads.pop(0) if self.reads else None

    def observe_process(self, pid, namespace, shell):
        self.observed.append((pid, namespace, shell))
        return SimpleNamespace(pid=pid, start=10, cwd="/work")

    def terminate(self):
        self.terminated += 1

    def close(self):
        self.closed += 1


def ref():
    return TerminalRef("session", "terminal")


def claim(**overrides):
    value = dict(mode="pipes", shell="/bin/bash", cwd="/work",
                 environment_id="env", ref=ref())
    value.update(overrides)
    return SimpleNamespace(**value)


def backend():
    value = ManagedBashBackend.__new__(ManagedBashBackend)
    value._environment = {"HOME": "/home"}
    value._environment_id = "env"
    value._cwd = "/work"
    value._process = Process()
    value._lock = threading.RLock()
    value._send_lock = threading.Lock()
    value._executing = threading.Lock()
    value._stopped = threading.Event()
    value._attempt = None
    value._generation = None
    value._retirement = None
    value._namespace = value._namespace_pid = None
    value._failed = False
    value._mode = value._active_ref = None
    value._decoder = wire.decoder()
    value._pending = deque()
    return value


def generation(value, attempt):
    item = TerminalGeneration(attempt, 10, 20)
    value._generation = item
    value._namespace_pid, value._namespace = 10, "namespace"
    return item


def test_send_chunks_and_rejects_a_stopped_owner():
    value = backend()
    value._send({"kind": "x", "value": "y"})
    assert value._process.writes
    value._stopped.set()
    with pytest.raises(RuntimeError, match="stopped"):
        value._send({"kind": "x"})


def test_next_decodes_output_and_reports_bad_events():
    value = backend()
    value._pending.append({"kind": "ready"})
    assert value._next() == {"kind": "ready"}
    value._pending.clear()
    value._process.reads = [SimpleNamespace(kind="output", stream="stdout",
                                            data=wire.encode({"kind": "ready"}))]
    assert value._next() == {"kind": "ready"}
    value._process.reads = [SimpleNamespace(kind="output", stream="stderr",
                                            data=b"")]
    with pytest.raises(RuntimeError, match="runtime error"):
        value._next()
    value._process.reads = [SimpleNamespace(kind="closed")]
    with pytest.raises(RuntimeError, match="ended"):
        value._next()
    value._stopped.set()
    with pytest.raises(RuntimeError, match="stopped"):
        value._next()


def test_next_honours_deadline_and_observation_budget():
    value = backend()
    value._process.reads = [SimpleNamespace(
        kind="output", stream="stdout",
        data=b"".join(wire.encode({"value": index}) for index in range(129)))]
    with pytest.raises(ValueError, match="queue"):
        value._next()
    value._pending.clear()
    with pytest.raises(TimeoutError):
        value._next(time.monotonic() - 1)


def test_start_validates_claim_and_establishes_generation(monkeypatch):
    value = backend()
    attempt = TerminalAttempt(ref(), 1, "host", "boot")
    value._wait = lambda _deadline: {
        "kind": "ready", "command": None, "pid": 10, "namespace": "namespace"}
    assert value.start(attempt, claim()).attempt is attempt
    assert value._generation.pid == 10
    assert value._process.started
    assert value._process.writes
    value._attempt = None
    with pytest.raises(ValueError, match="scope"):
        value.start(attempt, claim(mode="other"))
    value._attempt = attempt
    with pytest.raises(RuntimeError, match="occupied"):
        value.start(attempt, claim())


def test_start_rejects_ready_from_another_directory():
    value = backend()
    attempt = TerminalAttempt(ref(), 1, "host", "boot")
    value._wait = lambda _deadline: {
        "kind": "ready", "command": None, "pid": 10, "namespace": "namespace"}
    value._process.observe_process = lambda *_args: SimpleNamespace(
        pid=10, start=20, cwd="/other")
    with pytest.raises(ValueError, match="different directory"):
        value.start(attempt, claim())


def test_prepared_rejects_identity_and_directory_changes():
    value = backend()
    attempt = TerminalAttempt(ref(), 1, "host", "boot")
    generation(value, attempt)
    value._namespace_pid, value._namespace = 10, "namespace"
    request = SimpleNamespace(ref=SimpleNamespace(command_id="command"), cwd="/work")
    message = {"kind": "prepared", "command": "command", "pid": 10,
               "namespace": "namespace"}
    value._process.write = lambda _value: None
    value._process.observe_process = lambda *_args: SimpleNamespace(
        pid=10, start=20, cwd="/work")
    assert value._prepared(message, request)["cwd"] == "/work"
    with pytest.raises(ValueError, match="different shell"):
        value._prepared(dict(message, pid=11), request)
    value._process.observe_process = lambda *_args: SimpleNamespace(
        pid=10, start=99, cwd="/work")
    with pytest.raises(ValueError, match="identity"):
        value._prepared(message, request)
    value._process.observe_process = lambda *_args: SimpleNamespace(
        pid=10, start=20, cwd="/other")
    with pytest.raises(ValueError, match="directory"):
        value._prepared(dict(message, pid=10), request)


def test_execute_dispatches_output_and_completion():
    value = backend()
    attempt = TerminalAttempt(ref(), 1, "host", "boot")
    generation(value, attempt)
    command = "command"
    value._process.observe_process = lambda *_args: SimpleNamespace(
        pid=10, start=20, cwd="/work")
    value._send = lambda _message: None
    value._pending.extend((
        {"kind": "prepared", "command": command, "pid": 10,
         "namespace": "namespace"},
        {"kind": "started", "command": command},
        {"kind": "output", "command": command, "stream": "stdout",
         "data": base64.b64encode(b"out").decode()},
        {"kind": "complete", "command": command, "exit_code": 0},
    ))
    request = SimpleNamespace(ref=SimpleNamespace(command_id=command), cwd="/work")
    output, started = [], []
    completion = value._execute(
        request,
        lambda *args: started.append(args),
        lambda stream, data: output.append((stream, data)),
        lambda: False,
    )
    assert started and output == [("stdout", b"out")]
    assert completion.exit_code == 0 and completion.output_complete


@pytest.mark.parametrize("message", [
    {"kind": "failed", "command": "command"},
    {"kind": "unknown", "command": "command"},
])
def test_execute_rejects_unconfirmed_boundaries(message):
    value = backend()
    generation(value, TerminalAttempt(ref(), 1, "host", "boot"))
    value._send = lambda _message: None
    value._pending.extend(({"kind": "prepared", "command": "command", "pid": 10,
                             "namespace": "namespace"},
                            {"kind": "started", "command": "command"}, message))
    value._process.observe_process = lambda *_args: SimpleNamespace(
        pid=10, start=20, cwd="/work")
    with pytest.raises(RuntimeError):
        value._execute(SimpleNamespace(ref=SimpleNamespace(command_id="command"), cwd=None),
                       lambda *_args: None, lambda *_args: None, lambda: False)


def test_execute_guards_generation_mode_and_input(monkeypatch):
    value = backend()
    with pytest.raises(ValueError, match="generation"):
        value.execute(SimpleNamespace(), started=None, output=None, cancelled=lambda: False)
    value._failed = False
    attempt = TerminalAttempt(ref(), 1, "host", "boot")
    value._attempt, value._mode = attempt, "pipes"
    generation(value, attempt)
    request = CommandRequest(
        CommandRef("session", "terminal", "command"),
        "request", None, "env", mode="pipes", shell="/bin/bash", text="echo hi")
    value._execute = lambda *_args: "ok"
    assert value.execute(request, started=None, output=None, cancelled=lambda: False) == "ok"
    with pytest.raises(RuntimeError, match="cancelled"):
        value.execute(request, started=None, output=None, cancelled=lambda: True)


def test_close_is_idempotent_and_checks_attempt_owner():
    value = backend()
    attempt = TerminalAttempt(ref(), 1, "host", "boot")
    value._attempt = attempt
    first = value.close(attempt)
    assert first.kind == "owner_stopped" and value._process.closed == 1
    assert value.close(attempt) is first
    with pytest.raises(ValueError, match="another"):
        value.close(TerminalAttempt(ref(), 2, "host", "boot"))
