"""Writer I/O failures still stop the backend and leave replay-only evidence."""

import os
import threading

import pytest

from sico.service.terminal_backend import TerminalCompletion
from sico.service.terminal_dispatcher import TerminalUnavailable
from sico.storage.journal import SessionJournal
from session_terminal_fixtures import ControlledBackend, gated, owner, request, terminal


def test_real_journal_fsync_failure_closes_writer_but_still_cleans_backend(tmp_path, terminal, monkeypatch):
    with terminal(ControlledBackend(run=gated)) as (dispatch, backend, journal):
        first, second = request(), request("queued")
        dispatch.submit(first)
        assert backend.entered.wait(2)
        dispatch.submit(second)
        descriptor = journal._fd
        fsync = os.fsync
        failed = threading.Event()

        def fail_journal_once(fd):
            if fd == descriptor and not failed.is_set():
                failed.set()
                raise OSError("injected fsync failure")
            return fsync(fd)

        monkeypatch.setattr(os, "fsync", fail_journal_once)
        backend.release.set()
        result = dispatch.wait(first.ref, 3)
        assert failed.is_set() and backend.closed.wait(2)
        assert journal._fd == -1
        assert result.phase == "unknown" and result.output_bytes == 0
        assert backend.executions == [first]
    with SessionJournal(tmp_path, "session") as journal:
        backend = ControlledBackend()
        reopened = owner(journal, backend)
        assert reopened.snapshot()["terminal"]["phase"] == "unknown"
        assert reopened.wait(first.ref, 0).phase == "unknown"
        assert reopened.submit(first) == first.ref
        assert reopened.submit(second) == second.ref
        with pytest.raises(TerminalUnavailable):
            reopened.submit(request("new"))
        assert not backend.starts


@pytest.mark.parametrize("stream,data", [("pty", b"wrong mode"), ("stdout", b"x" * 65537)])
def test_invalid_current_output_latches_failure_even_if_backend_swallows_it(terminal, stream, data):
    seen = threading.Event()

    def invalid(_backend, _command, output, cancelled):
        try:
            output(stream, data)
        except ValueError:
            assert cancelled()
            seen.set()
        return TerminalCompletion(0, output_complete=True, shell_ready=True)

    with terminal(ControlledBackend(run=invalid)) as (dispatch, backend, _):
        command = request()
        dispatch.submit(command)
        assert dispatch.wait(command.ref, 2).phase == "unknown"
        assert seen.wait(2) and backend.closed.wait(2)


def test_unreadable_journal_cannot_block_process_cleanup(terminal, monkeypatch):
    with terminal(ControlledBackend(run=gated)) as (dispatch, backend, journal):
        command = request()
        dispatch.submit(command)
        assert backend.entered.wait(2)

        def damaged(*_args):
            raise OSError("injected read failure")

        # Force refresh to read an unrelated, committed journal row.
        journal.append("context.bound", {}, journal.state)
        monkeypatch.setattr(journal, "row", damaged)
        with pytest.raises(TerminalUnavailable):
            dispatch.close()
        assert backend.closed.is_set() and backend.stopped.is_set()


def test_unreadable_callback_stops_backend_even_when_error_is_swallowed(terminal, monkeypatch):
    stopped = threading.Event()

    def swallowing(backend, _command, output, cancelled):
        assert backend.release.wait(3)
        try:
            output("stdout", b"uncommitted")
        except OSError:
            assert cancelled()
            stopped.set()
        return TerminalCompletion(0, output_complete=True, shell_ready=True)

    with terminal(ControlledBackend(run=swallowing)) as (dispatch, backend, journal):
        dispatch.submit(request())
        assert backend.entered.wait(2)
        journal.append("context.bound", {}, journal.state)

        def damaged(*_args):
            raise OSError("injected read failure")

        monkeypatch.setattr(journal, "row", damaged)
        backend.release.set()
        assert stopped.wait(2) and backend.closed.wait(2)
