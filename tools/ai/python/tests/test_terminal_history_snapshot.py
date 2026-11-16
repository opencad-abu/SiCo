"""Live snapshots cannot promote bytes visible before the owning fsync completes."""

from concurrent.futures import ThreadPoolExecutor
import os
import threading

import pytest

from sico.core.terminal_output import OutputChunk
from sico.core.terminal_state import event
from sico.storage.journal import SessionJournal
from sico.storage.terminal_history import TerminalHistory
from terminal_history_fixtures import append, history, recorded


@pytest.mark.parametrize('failure', [False, True])
def test_live_snapshot_waits_for_commit_and_rejects_failed_writer(tmp_path, monkeypatch, failure):
    with SessionJournal(tmp_path, 'session') as journal:
        command, _ = recorded(journal, finish=False)
        query = TerminalHistory(journal)
        entered, release, requested, finished = (threading.Event() for _ in range(4))
        original, descriptor = os.fsync, journal._fd
        def fsync(fd):
            if fd == descriptor:
                entered.set()
                assert release.wait(5)
                if failure: raise OSError('injected fsync failure')
            return original(fd)
        def status():
            requested.set()
            try: return query.status(command.ref)
            finally: finished.set()
        with ThreadPoolExecutor(2) as pool, monkeypatch.context() as patch:
            patch.setattr(os, 'fsync', fsync)
            write = pool.submit(append, journal, event('output', command.ref,
                chunk=OutputChunk(command.ref, 1, 0, 'stdout', b'visible').record()))
            try:
                assert entered.wait(5)
                # The complete row is already visible in the file, but not committed.
                assert b'"kind": "output"' in (journal.directory/'events.jsonl').read_bytes()
                read = pool.submit(status)
                assert requested.wait(5) and not finished.wait(.05)
            finally:
                release.set()
            if failure:
                with pytest.raises(OSError): write.result(5)
                with pytest.raises(RuntimeError, match='closed'): read.result(5)
            else:
                write.result(5)
                assert read.result(5)['command']['output_bytes'] == 7


def test_independent_reader_cannot_claim_a_live_writers_visible_boundary(tmp_path):
    with SessionJournal(tmp_path, 'session') as journal:
        command, _ = recorded(journal)
        query, _ = history(journal.root)
        with pytest.raises(RuntimeError, match='owning writer'): query.status(command.ref)
        assert TerminalHistory(journal).status(command.ref)['command']['output_complete']
    assert query.status(command.ref)['command']['output_complete']


def test_closed_snapshot_keeps_writer_excluded_until_read_context_ends(tmp_path):
    with SessionJournal(tmp_path, 'session') as journal:
        recorded(journal)
        root = journal.root
    _, reader = history(root)
    with reader.snapshot_events() as events:
        with pytest.raises(RuntimeError, match='active writer'): SessionJournal(tmp_path, 'session')
        assert list(events()) == list(events())
    with SessionJournal(tmp_path, 'session') as reopened:
        assert len(reopened.records) == 3
