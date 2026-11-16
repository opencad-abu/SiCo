"""Reopen real session data without replaying input or losing output bytes."""

from hashlib import sha256

import pytest

from sico.core.contracts import RunState
from sico.core.terminal_output import OutputChunk
from sico.service.terminal_backend import TerminalCompletion
from sico.service.terminal_dispatcher import TerminalUnavailable
from sico.storage.history_reader import SessionReader
from sico.storage.journal import SessionJournal
from sico.storage.terminal_journal import TerminalJournal
from sico.storage.terminal_records import JOURNAL_KIND, restore
from session_terminal_fixtures import (
    CLAIM, CONTEXT, ControlledBackend, gated, owner, request, terminal,
)


def test_large_binary_output_survives_writer_close_and_hash_checked_reopen(tmp_path, terminal):
    data = ("中文输出\x00\x1b[31m\n" * 50000).encode() + b"\xff\xfe"

    def output_all(_backend, _command, output, _cancelled):
        for offset in range(0, len(data), 65536):
            output("stdout", data[offset:offset + 65536])
        output("stderr", b"error stream\n")
        return TerminalCompletion(0, output_complete=True, shell_ready=True)

    with terminal(ControlledBackend(run=output_all)) as (dispatch, _, journal):
        command = request()
        dispatch.submit(command)
        state = dispatch.wait(command.ref, 4)
        assert state.output_bytes == len(data) + len(b"error stream\n")
        assert state.output_complete
        root = journal.root
    reader = SessionReader(root, "session")
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    for row in reader.stream_events():
        if row["kind"] == JOURNAL_KIND:
            fact = restore(row["payload"], reader)
            if fact["kind"] == "output":
                chunk = OutputChunk.from_record(fact["chunk"])
                streams[chunk.stream].extend(chunk.data)
    assert sha256(streams["stdout"]).digest() == sha256(data).digest()
    assert streams["stderr"] == b"error stream\n"
    with SessionJournal(tmp_path, "session") as journal:
        backend = ControlledBackend()
        reopened = owner(journal, backend)
        assert reopened.wait(command.ref, 0) == state
        assert reopened.submit(command) == command.ref
        assert not backend.starts and not backend.executions
        with pytest.raises(TerminalUnavailable):
            reopened.submit(request("new"))
        reopened.close()


def test_recovered_pending_queue_is_queryable_and_never_automatically_dispatched(tmp_path, terminal):
    with terminal(ControlledBackend(run=gated)) as (dispatch, backend, _):
        first, queued = request(), request("queued")
        dispatch.submit(first)
        assert backend.entered.wait(2)
        dispatch.submit(queued)
        dispatch.close()
        assert backend.executions == [first]
    with SessionJournal(tmp_path, "session") as journal:
        backend = ControlledBackend()
        reopened = owner(journal, backend)
        assert reopened.snapshot()["terminal"]["queued_commands"] == [queued.ref.record()]
        assert reopened.wait(queued.ref, 0).phase == "unknown"
        assert reopened.submit(queued) == queued.ref
        with pytest.raises(TerminalUnavailable):
            reopened.submit(request("new"))
        assert not backend.starts and not backend.executions
        reopened.close()


def test_terminal_commits_preserve_current_model_task_state(terminal):
    with terminal(ControlledBackend(run=gated)) as (dispatch, backend, journal):
        command = request()
        dispatch.submit(command)
        assert backend.entered.wait(2)
        current = RunState(CONTEXT)
        # A newer session context stands in for concurrently committed model facts.
        from dataclasses import replace
        current = replace(current, context=replace(CONTEXT, target_id="changed-target"))
        journal.append("context.bound", {}, current)
        backend.release.set()
        assert dispatch.wait(command.ref, 3).succeeded
        assert journal.state.context == current.context


def test_command_lookup_remains_correct_after_bounded_cache_eviction(terminal, monkeypatch):
    import sico.storage.terminal_journal as module

    monkeypatch.setattr(module, "COMMAND_CACHE", 2)
    with terminal() as (dispatch, _, journal):
        commands = [request(str(index)) for index in range(5)]
        for command in commands:
            dispatch.submit(command)
            assert dispatch.wait(command.ref, 2).succeeded
        records = TerminalJournal(journal, CONTEXT, CLAIM)
        assert len(records._commands) <= 2
        assert records.command(commands[0].ref).succeeded
        assert records.duplicate(commands[0]) == commands[0].ref
        assert len(records._commands) <= 2
