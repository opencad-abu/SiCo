"""Opt-in fixed-runtime PTY input and foreground cancellation evidence."""

from dataclasses import replace
import threading
import time

import pytest

from bash_channel_fixtures import command
from exec_live_fixtures import managed_configuration
from session_terminal_fixtures import CLAIM, CONTEXT
from sico.core.terminal_output import OutputChunk
from sico.core.terminal_input import TerminalInput
from sico.core.terminal_session import TerminalAttempt
from sico.execution.managed_bash import ManagedBashBackend
from sico.process_identity import boot_id
from sico.service.terminal_dispatcher import TerminalSessionDispatcher
from sico.storage.history_reader import SessionReader
from sico.storage.journal import SessionJournal
from sico.storage.terminal_records import JOURNAL_KIND, restore


def pty_backend(configuration, access="read-only"):
    binary, control, child, work, home = configuration
    claim = replace(CLAIM, cwd=str(work), mode="pty")
    backend = ManagedBashBackend(
        binary, control, cwd=work, home=home, environment=child,
        environment_id=claim.environment_id,
        access=dict(sandbox=access, approval="never", network=False))
    return backend, claim


def run_backend(backend, request, *, cancel=None, on_started=None):
    result, error = [], []
    streams = {stream: bytearray() for stream in ("pty",)}

    def run():
        try:
            result.append(backend.execute(
                request, started=lambda *args: on_started and on_started(*args),
                output=lambda stream, data: streams[stream].extend(data),
                cancelled=cancel.is_set if cancel is not None else (lambda: False)))
        except BaseException as exc:
            error.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker, result, error, streams


def test_pty_stdin_is_delivered_to_the_same_managed_shell(managed_configuration):
    backend, claim = pty_backend(managed_configuration)
    creation = TerminalAttempt(claim.ref, 1, "pty-fixture", boot_id())
    generation = backend.start(creation, claim)
    try:
        request = command(
            'printf ready; read line; printf "got:%s" "$line"',
            "input", mode="pty")
        started = threading.Event()
        worker, result, error, streams = run_backend(
            backend, request, on_started=lambda *_args: started.set())
        assert started.wait(5), error
        backend.write_input(TerminalInput(request.ref, "user", 1, b"hello" + bytes((10,))))
        worker.join(15)
        assert not error
        assert result[0].output_complete and result[0].shell_ready
        assert not result[0].cancelled and result[0].exit_code == 0
        assert b"got:hello" in streams["pty"]
        with pytest.raises(ValueError, match="active PTY"):
            backend.write_input(TerminalInput(request.ref, "user", 2, b"late"))

        second = command('printf "%s" "$?"', "second", mode="pty")
        worker, result, error, streams = run_backend(
            backend, second, on_started=lambda *_args: None)
        worker.join(10)
        assert not error and result[0].exit_code == 0
        assert b"0" in streams["pty"]
        assert backend._generation == generation
    finally:
        backend.close(creation)


def test_pty_background_tail_remains_with_its_command(managed_configuration):
    backend, claim = pty_backend(managed_configuration)
    creation = TerminalAttempt(claim.ref, 1, "pty-tail-fixture", boot_id())
    backend.start(creation, claim)
    try:
        request = command('(sleep .2; printf late) & printf now', "tail", mode="pty")
        worker, result, error, streams = run_backend(backend, request)
        worker.join(10)
        assert not error and result[0].output_complete and result[0].exit_code == 0
        assert b"now" in streams["pty"] and b"late" in streams["pty"]
        second = command('printf "%s" "$?"', "after-tail", mode="pty")
        worker, result, error, streams = run_backend(backend, second)
        worker.join(10)
        assert not error and result[0].exit_code == 0
        assert b"0" in streams["pty"]
    finally:
        backend.close(creation)


def test_pty_control_loss_keeps_the_tail_but_does_not_publish_completion(managed_configuration):
    backend, claim = pty_backend(managed_configuration)
    creation = TerminalAttempt(claim.ref, 1, "pty-loss-fixture", boot_id())
    backend.start(creation, claim)
    try:
        request = command("printf tail; exec /bin/true; printf forbidden", "loss", mode="pty")
        worker, result, error, streams = run_backend(backend, request)
        worker.join(10)
        assert not result and error and isinstance(error[0], RuntimeError)
        assert b"tail" in streams["pty"] and b"forbidden" not in streams["pty"]
    finally:
        backend.close(creation)


def test_pty_foreground_cancel_keeps_the_shell_and_does_not_run_the_tail(managed_configuration):
    backend, claim = pty_backend(managed_configuration, "workspace-write")
    creation = TerminalAttempt(claim.ref, 1, "pty-cancel-fixture", boot_id())
    generation = backend.start(creation, claim)
    try:
        request = command(
            'printf ready; sleep 30; printf forbidden', "cancel", mode="pty")
        started, cancel = threading.Event(), threading.Event()
        worker, result, error, streams = run_backend(
            backend, request, cancel=cancel,
            on_started=lambda *_args: started.set())
        assert started.wait(5), error
        deadline = time.monotonic() + 5
        while b"ready" not in streams["pty"] and time.monotonic() < deadline:
            time.sleep(.01)
        assert b"ready" in streams["pty"]
        cancel.set()
        worker.join(15)
        assert not error
        assert result[0].cancelled and result[0].exit_code == 130
        assert result[0].output_complete and result[0].shell_ready
        assert b"ready" in streams["pty"] and b"forbidden" not in streams["pty"]

        following = command('printf "%s" "$?"', "following", mode="pty")
        worker, result, error, streams = run_backend(
            backend, following, on_started=lambda *_args: None)
        worker.join(10)
        assert not error and result[0].exit_code == 0
        assert b"130" in streams["pty"]
        assert backend._generation == generation
    finally:
        backend.close(creation)


def test_dispatcher_commits_pty_output_and_input_ownership_without_rerun(managed_configuration, tmp_path):
    backend, claim = pty_backend(managed_configuration)
    request = command(
        'read line; printf "owned:%s" "$line"', "dispatched", mode="pty")
    with SessionJournal(tmp_path, claim.ref.session_id) as journal:
        owner = TerminalSessionDispatcher(
            journal, CONTEXT, claim,
            backend=backend, host_id="pty-dispatcher", boot_id=boot_id())
        try:
            owner.submit(request)
            deadline = time.monotonic() + 5
            while (owner.command(request.ref).phase != "running"
                   and time.monotonic() < deadline):
                time.sleep(.01)
            assert owner.command(request.ref).phase == "running"
            owner.take_input(request.ref, "model")
            owner.write_input(request.ref, "model", b"model-value" + bytes((10,)))
            state = owner.wait(request.ref, 10)
            assert state.succeeded and state.output_complete
            assert state.input_bytes == 12 and state.input_chunks == 1
            assert owner.snapshot()["terminal"]["input_owner"] is None
        finally:
            owner.close()
        root = journal.root
    query = SessionReader(root, claim.ref.session_id)
    captured = bytearray()
    for row in query.stream_events():
        if row["kind"] == JOURNAL_KIND:
            fact = restore(row["payload"], query)
            if fact["kind"] == "output":
                chunk = OutputChunk.from_record(fact["chunk"])
                assert chunk.stream == "pty"
                captured.extend(chunk.data)
    assert b"owned:model-value" in captured
