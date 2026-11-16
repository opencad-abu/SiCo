"""Real managed ownership under concurrent admission and durable-write failures."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import threading
import time

import pytest

from bash_channel_fixtures import command
from exec_live_fixtures import managed_configuration
from test_managed_bash_backend_live import managed_backend, output_records
from session_terminal_fixtures import CONTEXT
from sico.process_identity import boot_id, process_identity
from sico.service.terminal_dispatcher import TerminalSessionDispatcher, TerminalUnavailable
from sico.storage.journal import SessionJournal


def owner(journal, claim, backend):
    return TerminalSessionDispatcher(journal, CONTEXT, claim, backend=backend,
                                     host_id='managed-fixture', boot_id=boot_id())


def eventually(predicate):
    deadline = time.monotonic() + 8
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail('Managed ownership observation timed out')
        threading.Event().wait(.01)


def gone(generation):
    try:
        value = process_identity(generation.pid)
        return value['start'] != str(generation.process_start) or value['state'] in ('Z', 'X')
    except (FileNotFoundError, ProcessLookupError):
        return True


def test_concurrent_retries_admit_once_and_bounded_fifo_keeps_one_shell(managed_backend, tmp_path):
    backend, claim = managed_backend()
    claim = replace(claim, queue_limit=2)
    first = command('while ! test -f permit; do sleep .01; done; export ORDER=first; printf first')
    second = command('printf "%s:second" "$ORDER"; ORDER=second', 'second')
    third = command('printf "%s:third" "$ORDER"', 'third')
    barrier = threading.Barrier(8)
    def submit(value):
        barrier.wait(timeout=5)
        return value.submit(first)
    with SessionJournal(tmp_path, 'session') as journal:
        value = owner(journal, claim, backend)
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(submit, value) for _ in range(8)]
                assert [future.result(timeout=10) for future in futures] == [first.ref] * 8
            eventually(lambda: value._records.command(first.ref).phase == 'running')
            generation = backend._generation
            value.submit(second)
            value.submit(third)
            with pytest.raises(ValueError, match='queue is full'):
                value.submit(command('printf forbidden', 'fourth'))
            assert value.snapshot()['terminal']['queued_commands'] == [second.ref.record(), third.ref.record()]
            (Path(claim.cwd) / 'permit').touch()
            states = [value.wait(request.ref, 10) for request in (first, second, third)]
            assert all(state.succeeded and state.output_complete for state in states)
            assert all(state.generation == generation for state in states)
            accepted = [journal.row(i)['payload'] for i in range(len(journal.records))
                        if journal.row(i)['payload'].get('kind') == 'accepted']
            assert len(accepted) == 3
        finally:
            value.close()
        root = journal.root
    output = output_records(root, (first, second, third))
    assert [bytes(output[r.ref.command_id]['stdout']) for r in (first, second, third)] == [
        b'first', b'first:second', b'second:third']
    assert gone(generation)


@pytest.mark.parametrize('failure', ['artifact', 'journal'])
def test_output_fsync_failure_stops_queue_and_reaps_owner(managed_backend, tmp_path, monkeypatch, failure):
    backend, claim = managed_backend()
    first = command('while ! test -f permit; do sleep .01; done; printf output; sleep 30')
    second = command('printf must-not-run', 'second')
    with SessionJournal(tmp_path, 'session') as journal:
        value = owner(journal, claim, backend)
        value.submit(first)
        eventually(lambda: value._records.command(first.ref).phase == 'running')
        value.submit(second)
        generation, original = backend._generation, os.fsync
        failed = threading.Event()
        journal_fd = journal._fd
        def fsync(fd):
            path = os.readlink('/proc/self/fd/' + str(fd))
            selected = fd == journal_fd if failure == 'journal' else path.startswith(str(journal.attachments))
            if selected and not failed.is_set():
                failed.set()
                raise OSError('Injected output fsync failure')
            return original(fd)
        monkeypatch.setattr(os, 'fsync', fsync)
        (Path(claim.cwd) / 'permit').touch()
        eventually(failed.is_set)
        eventually(lambda: backend._retirement is not None)
        assert gone(generation)
        assert value.snapshot()['fault']
        with pytest.raises(TerminalUnavailable):
            value.submit(command('printf forbidden', 'third'))
        assert value._records.command(second.ref).phase == 'accepted'
        # The failed writer cannot publish retirement; cleanup is still confirmed
        # by the backend, while the persistent reservation stays unresolved.
        assert value.snapshot()['terminal']['phase'] == 'unknown'
        with pytest.raises(TerminalUnavailable):
            value.close()


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_startup_identity_failure_never_releases_unconfirmed_owner(managed_backend, tmp_path, monkeypatch, cleanup_fails):
    backend, claim = managed_backend()
    process = backend._process
    original_close = process.close
    close_failed = threading.Event()
    def reject(*args):
        raise ValueError('Injected host identity mismatch')
    def close():
        if cleanup_fails:
            close_failed.set()
            raise RuntimeError('Injected unconfirmed cleanup')
        original_close()
    monkeypatch.setattr(process, 'observe_process', reject)
    monkeypatch.setattr(process, 'close', close)
    with SessionJournal(tmp_path, 'session') as journal:
        value = owner(journal, claim, backend)
        try:
            with pytest.raises(TerminalUnavailable):
                value.submit(command())
            if cleanup_fails:
                eventually(close_failed.is_set)
                assert backend._retirement is None
                assert value.snapshot()['terminal']['phase'] == 'unknown'
            else:
                eventually(lambda: value.snapshot()['terminal']['phase'] == 'retired')
                assert backend._retirement.kind == 'owner_stopped'
            with pytest.raises(TerminalUnavailable):
                value.submit(command('printf forbidden', 'second'))
        finally:
            monkeypatch.setattr(process, 'close', original_close)
            value.close()
        assert backend._retirement.kind == 'owner_stopped'


def test_close_during_verified_startup_reaps_generation_without_dispatch(managed_backend, tmp_path, monkeypatch):
    backend, claim = managed_backend()
    observed, release = threading.Event(), threading.Event()
    original = backend._process.observe_process
    def observe(*args):
        result = original(*args)
        observed.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(backend._process, 'observe_process', observe)
    with SessionJournal(tmp_path, 'session') as journal:
        value = owner(journal, claim, backend)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                submitted = pool.submit(value.submit, command('printf must-not-run'))
                assert observed.wait(8)
                closed = pool.submit(value.close)
                eventually(lambda: value.snapshot()['closing'])
                release.set()
                closed.result(timeout=8)
                with pytest.raises(TerminalUnavailable): submitted.result(timeout=8)
            assert backend._retirement.kind == 'process_exited'
            assert gone(backend._generation)
            assert not any(journal.row(i)['payload'].get('kind') == 'accepted'
                           for i in range(len(journal.records)))
        finally:
            release.set()
            value.close()
