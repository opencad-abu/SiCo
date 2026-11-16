"""Controlled backend observations; these fixtures do not execute a real shell."""

from contextlib import contextmanager
import threading

import pytest

from sico.core.contracts import BoundContext
from sico.core.terminal_commands import CommandRef, CommandRequest
from sico.core.terminal_session import TerminalClaim, TerminalGeneration, TerminalRef, TerminalRetirement
from sico.service.terminal_backend import TerminalCompletion
from sico.service.terminal_dispatcher import TerminalSessionDispatcher, TerminalUnavailable
from sico.storage.journal import SessionJournal
from sico.storage.terminal_records import JOURNAL_KIND, restore


CONTEXT = BoundContext("instance", "generation", "target", {})
CLAIM = TerminalClaim(TerminalRef("session", "terminal"), "/bin/bash", "/project", "environment",
                      mode="pipes")


def request(name="first", **fields):
    values = dict(ref=CommandRef("session", "terminal", name), request_id="request-" + name,
                  cwd=None, environment_id="environment", shell="/bin/bash", text="pwd")
    values.update(fields)
    return CommandRequest(**values)


class ControlledBackend:
    def __init__(self, *, run=None, start=None, close=None):
        self.run_hook, self.start_hook, self.close_hook = run, start, close
        self.starts, self.executions, self.callbacks = [], [], []
        self.inputs = []
        self.generation = None
        self.closed = threading.Event()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.stopped = threading.Event()
        self._lock = threading.Lock()

    def start(self, attempt, claim):
        assert attempt.ref == claim.ref
        self.claim = claim
        with self._lock:
            if self.closed.is_set():
                raise RuntimeError("Fenced before spawn")
            self.starts.append(attempt)
            self.generation = TerminalGeneration(attempt, 123, 456)
        if self.start_hook:
            self.start_hook(self, attempt)
        if self.closed.is_set():
            raise RuntimeError("Startup was stopped")
        return self.generation

    def execute(self, command, *, started, output, cancelled):
        self.executions.append(command)
        self.callbacks.append((started, output, cancelled))
        started("execution-" + command.ref.command_id)
        self.entered.set()
        try:
            if self.run_hook:
                return self.run_hook(self, command, output, cancelled)
            output("stdout", b"/project\n")
            output("stderr", b"diagnostic\n")
            return TerminalCompletion(0, output_complete=True, shell_ready=True)
        finally:
            self.stopped.set()

    def write_input(self, value):
        self.inputs.append(value)

    def close(self, attempt):
        self.closed.set()
        self.release.set()
        if self.close_hook:
            return self.close_hook(self, attempt)
        return TerminalRetirement(attempt, "process_exited" if self.generation else "spawn_failed",
                                  "controlled-receipt", self.generation)


def gated(backend, command, output, cancelled):
    assert backend.release.wait(4), "test did not release execution"
    if cancelled():
        return TerminalCompletion(None, signal=2, output_complete=True, shell_ready=True)
    output("stdout", command.ref.command_id.encode())
    return TerminalCompletion(0, output_complete=True, shell_ready=True)


def owner(journal, backend, claim=CLAIM):
    return TerminalSessionDispatcher(journal, CONTEXT, claim, backend=backend,
                                     host_id="host", boot_id="boot")


@pytest.fixture
def terminal(tmp_path):
    @contextmanager
    def opened(backend=None, claim=CLAIM):
        backend = backend or ControlledBackend()
        with SessionJournal(tmp_path, claim.ref.session_id) as journal:
            dispatcher = owner(journal, backend, claim)
            try:
                yield dispatcher, backend, journal
            finally:
                backend.release.set()
                try:
                    dispatcher.close()
                except TerminalUnavailable:
                    pass  # Failure tests inspect the retained occupancy themselves.
    return opened


def observations(journal):
    return [restore(row["payload"], journal) for row in journal.records
            if row["kind"] == JOURNAL_KIND]


def fail_append(monkeypatch, journal, predicate):
    append = journal.append

    def failing(kind, payload, state):
        if kind == JOURNAL_KIND and predicate(payload):
            raise OSError("injected journal failure")
        return append(kind, payload, state)

    monkeypatch.setattr(journal, "append", failing)
