"""A failed commit or uncertain process cannot silently release terminal ownership."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading

import pytest

from sico.service.terminal_backend import TerminalCompletion
from sico.service.terminal_dispatcher import TerminalUnavailable
from sico.storage.terminal_journal import TerminalPersistenceError
from session_terminal_fixtures import (
    ControlledBackend, fail_append, gated, observations, owner, request, terminal,
)


def test_creation_commit_failure_never_calls_start(terminal, monkeypatch):
    with terminal() as (dispatch, backend, journal):
        fail_append(monkeypatch, journal, lambda value: value["kind"] == "creation_requested")
        with pytest.raises(TerminalPersistenceError):
            dispatch.submit(request())
        assert not backend.starts
        assert dispatch.snapshot()["terminal"]["phase"] == "claimed"
        with pytest.raises(TerminalUnavailable):
            dispatch.submit(request("later"))


@pytest.mark.parametrize("step", ["started", "ready", "resumed"])
def test_startup_commit_failure_retains_creation_and_never_fakes_spawn_failure(terminal, monkeypatch, step):
    with terminal() as (dispatch, backend, journal):
        fail_append(monkeypatch, journal, lambda value: value["kind"] == step)
        with pytest.raises(TerminalUnavailable):
            dispatch.submit(request())
        assert backend.closed.wait(2)
        assert len(backend.starts) == 1 and not backend.executions
        assert dispatch.snapshot()["terminal"]["phase"] == "unknown"
        facts = observations(journal)
        assert not any(fact["kind"] in ("retired", "accepted") for fact in facts)
        other = owner(journal, ControlledBackend())
        with pytest.raises(TerminalUnavailable):
            other.submit(request("other"))


@pytest.mark.parametrize("step", ["accepted", "queued", "dispatched", "started"])
def test_admission_and_dispatch_failures_do_not_send_uncommitted_input(terminal, monkeypatch, step):
    with terminal() as (dispatch, backend, journal):
        fail_append(monkeypatch, journal, lambda value: value["kind"] == step
                    and (step != "started" or "execution_id" in value))
        command = request()
        if step in ("accepted", "queued"):
            with pytest.raises(TerminalPersistenceError):
                dispatch.submit(command)
        else:
            dispatch.submit(command)
            assert dispatch.wait(command.ref, 2).phase == "unknown"
        assert backend.closed.wait(2)
        assert len(backend.executions) == (1 if step == "started" else 0)
        facts = observations(journal)
        assert not any(fact["kind"] in ("exited", "finished", "retired") for fact in facts)
        if step != "accepted":
            assert dispatch.submit(command) == command.ref  # Retry queries the accepted identity.
        with pytest.raises(TerminalUnavailable):
            dispatch.submit(request("later"))


@pytest.mark.parametrize("failure", ["artifact", "output", "exited", "finished"])
def test_persistence_failure_keeps_only_committed_prefix_and_stops_next_command(terminal, monkeypatch, failure):
    with terminal(ControlledBackend(run=gated)) as (dispatch, backend, journal):
        first, second = request(), request("second")
        dispatch.submit(first)
        assert backend.entered.wait(2)
        dispatch.submit(second)
        if failure == "artifact":
            def full(*_args, **_kwargs):
                raise OSError("injected artifact failure")
            monkeypatch.setattr(journal, "artifact", full)
        else:
            fail_append(monkeypatch, journal, lambda value: value["kind"] == failure)
        backend.release.set()
        result = dispatch.wait(first.ref, 3)
        assert backend.closed.wait(2)
        assert backend.executions == [first]
        facts = observations(journal)
        if failure in ("artifact", "output"):
            assert result.output_bytes == 0 and result.output_sequence == 0
            assert not any(fact["kind"] == "output" for fact in facts)
        else:
            assert result.output_bytes == len(b"first")
        assert not any(fact["kind"] == "finished" for fact in facts)
        assert result.phase == ("exited" if failure == "finished" else "unknown")
        assert not result.output_complete if failure != "finished" else result.output_complete
        assert dispatch.snapshot()["terminal"]["queued_commands"] == [second.ref.record()]


def test_swallowed_callback_failure_still_cancels_backend_and_cannot_publish_success(terminal, monkeypatch):
    cancellation_seen = threading.Event()

    def swallowing(_backend, _command, output, cancelled):
        try:
            output("stdout", b"uncommitted")
        except TerminalPersistenceError:
            if cancelled():
                cancellation_seen.set()
        return TerminalCompletion(0, output_complete=True, shell_ready=True)

    with terminal(ControlledBackend(run=swallowing)) as (dispatch, backend, journal):
        fail_append(monkeypatch, journal, lambda value: value["kind"] == "output")
        command = request()
        dispatch.submit(command)
        assert dispatch.wait(command.ref, 2).phase == "unknown"
        assert cancellation_seen.wait(2) and backend.closed.wait(2)
        assert not any(fact["kind"] == "exited" for fact in observations(journal))


def test_failed_cancellation_is_not_reported_as_committed_or_completed(terminal, monkeypatch):
    with terminal(ControlledBackend(run=gated)) as (dispatch, backend, journal):
        command = request()
        dispatch.submit(command)
        assert backend.entered.wait(2)
        fail_append(monkeypatch, journal, lambda value: value["kind"] == "cancel_requested")
        with pytest.raises(TerminalPersistenceError):
            dispatch.cancel(command.ref)
        state = dispatch.wait(command.ref, 2)
        assert not state.cancel_requested and state.phase == "unknown"
        assert not any(fact["kind"] == "exited" for fact in observations(journal))


def test_close_interrupts_blocked_execution_without_holding_callback_lock(terminal):
    with terminal(ControlledBackend(run=gated)) as (dispatch, backend, _):
        command = request()
        dispatch.submit(command)
        assert backend.entered.wait(2)
        dispatch.close(timeout=2)
        assert backend.stopped.is_set()
        assert dispatch.snapshot()["terminal"]["phase"] == "retired"
        assert dispatch.wait(command.ref, 0).phase == "unknown"
        with pytest.raises(TerminalUnavailable):
            backend.callbacks[0][1]("stdout", b"late bytes")


def test_close_interrupts_start_and_fences_late_creation(terminal):
    entered = threading.Event()

    def blocking_start(backend, _attempt):
        entered.set()
        assert backend.release.wait(3)

    with terminal(ControlledBackend(start=blocking_start)) as (dispatch, backend, _):
        with ThreadPoolExecutor(max_workers=1) as pool:
            submitted = pool.submit(dispatch.submit, request())
            assert entered.wait(2)
            dispatch.close()
            with pytest.raises(TerminalUnavailable):
                submitted.result(2)
        assert backend.closed.is_set() and not backend.executions
        assert dispatch.snapshot()["terminal"]["phase"] == "retired"


def test_unconfirmed_descendant_cleanup_preserves_occupancy(terminal):
    def unconfirmed(_backend, _attempt):
        raise RuntimeError("descendant still alive")

    with terminal(ControlledBackend(run=gated, close=unconfirmed)) as (dispatch, backend, journal):
        command = request()
        dispatch.submit(command)
        assert backend.entered.wait(2)
        with pytest.raises(TerminalUnavailable):
            dispatch.close()
        assert dispatch.snapshot()["terminal"]["phase"] == "unknown"
        assert not any(fact["kind"] == "retired" for fact in observations(journal))
        recovered = owner(journal, ControlledBackend())
        with pytest.raises(TerminalUnavailable):
            recovered.submit(request("later"))


def test_completion_without_shell_boundary_cannot_advance_fifo(terminal):
    def missing_boundary(backend, command, output, cancelled):
        gated(backend, command, output, cancelled)
        return TerminalCompletion(0, output_complete=True, shell_ready=False)

    with terminal(ControlledBackend(run=missing_boundary)) as (dispatch, backend, journal):
        first, second = request(), request("second")
        dispatch.submit(first)
        assert backend.entered.wait(2)
        dispatch.submit(second)
        backend.release.set()
        assert dispatch.wait(first.ref, 2).succeeded
        assert backend.closed.wait(2)
        assert backend.executions == [first]
        assert not any(fact["kind"] == "finished" for fact in observations(journal))


def test_late_output_cannot_be_attributed_to_next_command(terminal):
    second_entered = threading.Event()

    def run(backend, command, output, cancelled):
        if command.ref.command_id == "second":
            second_entered.set()
            return gated(backend, command, output, cancelled)
        output("stdout", b"first")
        return TerminalCompletion(0, output_complete=True, shell_ready=True)

    with terminal(ControlledBackend(run=run)) as (dispatch, backend, journal):
        first, second = request(), request("second")
        dispatch.submit(first)
        dispatch.wait(first.ref, 2)
        dispatch.submit(second)
        assert second_entered.wait(2)
        with pytest.raises(TerminalUnavailable):
            backend.callbacks[0][1]("stdout", b"late first")
        backend.release.set()
        assert dispatch.wait(second.ref, 2).output_bytes == len(b"second")


def test_wrong_generation_does_not_start_execution(terminal):
    def wrong_generation(backend, _attempt):
        backend.generation = replace(backend.generation,
                                     attempt=replace(backend.generation.attempt, epoch=2))

    with terminal(ControlledBackend(start=wrong_generation)) as (dispatch, backend, _):
        with pytest.raises(TerminalUnavailable):
            dispatch.submit(request())
        assert not backend.executions
