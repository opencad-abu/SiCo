"""Persistent scheduling, identity and cancellation using a controlled backend."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading

import pytest

from sico.core.terminal_commands import CommandRef
from sico.core.terminal_session import TerminalRef
from sico.service.terminal_backend import TerminalCompletion
from sico.service.terminal_dispatcher import TerminalUnavailable
from session_terminal_fixtures import (
    CLAIM, ControlledBackend, gated, observations, owner, request, terminal,
)


def test_fifo_reuses_one_backend_and_commits_before_external_effects(terminal):
    with terminal(ControlledBackend(run=gated)) as (dispatch, backend, journal):
        first, second, third = [request(name) for name in ("first", "second", "third")]

        def check_start(_backend, _attempt):
            assert observations(journal)[-1]["kind"] == "creation_requested"

        backend.start_hook = check_start
        dispatch.submit(first)
        assert backend.entered.wait(2)
        dispatch.submit(second)
        dispatch.submit(third)
        state = dispatch.snapshot()["terminal"]
        assert state["active_command"] == first.ref.record()
        assert state["queued_commands"] == [second.ref.record(), third.ref.record()]
        backend.release.set()
        for command in (first, second, third):
            assert dispatch.wait(command.ref, 3).succeeded
        assert len(backend.starts) == 1
        assert backend.claim == CLAIM
        assert backend.executions == [first, second, third]
        assert all(command.cwd is None for command in backend.executions)
        facts = observations(journal)
        for command in (first, second, third):
            kinds = [fact["kind"] for fact in facts if fact.get("ref") == command.ref.record()
                     or fact.get("command") == command.ref.record()]
            assert kinds == ["accepted", "queued", "dispatched", "started", "output", "exited", "finished"]


def test_queue_full_rejects_before_acceptance_and_retry_is_idempotent(terminal):
    with terminal(ControlledBackend(run=gated), replace(CLAIM, queue_limit=1)) as (dispatch, backend, journal):
        first, second, rejected = [request(name) for name in ("first", "second", "third")]
        dispatch.submit(first)
        assert backend.entered.wait(2)
        dispatch.submit(second)
        for command in (first, second):
            assert dispatch.submit(command) == command.ref
        with pytest.raises(ValueError, match="full"):
            dispatch.submit(rejected)
        assert [fact["request"]["request_id"] for fact in observations(journal)
                if fact["kind"] == "accepted"] == [first.request_id, second.request_id]


@pytest.mark.parametrize("change", ["request_id", "ref"])
def test_identity_conflicts_do_not_execute_again(terminal, change):
    with terminal() as (dispatch, backend, _):
        first = request()
        dispatch.submit(first)
        dispatch.wait(first.ref, 2)
        conflicting = replace(request("other"), **{change: getattr(first, change)})
        with pytest.raises(ValueError, match="identity conflict"):
            dispatch.submit(conflicting)
        assert backend.executions == [first]


@pytest.mark.parametrize("fields", [
    {"ref": CommandRef("other", "terminal", "first")},
    {"ref": CommandRef("session", "other", "first")},
    {"environment_id": "other"}, {"shell": "/bin/zsh"}, {"mode": "pty"},
])
def test_mismatched_claim_is_rejected_without_spawning(terminal, fields):
    with terminal() as (dispatch, backend, _):
        with pytest.raises(ValueError, match="claim"):
            dispatch.submit(request(**fields))
        assert not backend.starts


def test_simultaneous_submissions_keep_one_start_and_one_execution_per_request(terminal):
    with terminal() as (dispatch, backend, _):
        commands = [request(str(index)) for index in range(16)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            refs = list(pool.map(dispatch.submit, commands * 2))
        assert set(refs) == {command.ref for command in commands}
        for command in commands:
            assert dispatch.wait(command.ref, 4).succeeded
        assert len(backend.starts) == 1 and len(backend.executions) == 16


def test_two_attachments_to_one_writer_cannot_start_two_backends(terminal):
    with terminal() as (first, backend, journal):
        other_backend = ControlledBackend()
        second = owner(journal, other_backend)
        barrier = threading.Barrier(2)

        def submit(dispatch, command):
            barrier.wait(2)
            try:
                return dispatch.submit(command)
            except (ValueError, TerminalUnavailable):
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(submit, first, request()), pool.submit(submit, second, request("other"))]
            refs = [job.result(4) for job in jobs]
        assert sum(ref is not None for ref in refs) == 1
        assert len(backend.starts) + len(other_backend.starts) == 1
        second.close()


def test_another_terminal_claim_cannot_attach_to_same_session(terminal):
    with terminal() as (_, _, journal):
        with pytest.raises(ValueError, match="different terminal claim"):
            owner(journal, ControlledBackend(), replace(CLAIM, ref=TerminalRef("session", "other")))


def test_queued_cancel_never_executes_and_active_cancel_is_only_a_request(terminal):
    with terminal(ControlledBackend(run=gated)) as (dispatch, backend, journal):
        first, second = request(), request("second")
        dispatch.submit(first)
        assert backend.entered.wait(2)
        dispatch.submit(second)
        dispatch.cancel(second.ref)
        assert dispatch.wait(second.ref, 1).phase == "not_started"
        dispatch.cancel(first.ref)
        facts = observations(journal)
        assert facts[-1]["kind"] == "cancel_requested"
        assert backend.callbacks[0][2]()
        assert not any(fact["kind"] == "exited" for fact in facts)
        backend.release.set()
        state = dispatch.wait(first.ref, 2)
        assert state.phase == "exited" and state.signal == 2 and state.cancel_requested
        assert backend.executions == [first]


@pytest.mark.parametrize("fields", [
    {"exit_code": None}, {"exit_code": True}, {"exit_code": 256},
    {"exit_code": 0, "signal": 2}, {"exit_code": None, "signal": 0},
    {"exit_code": 0, "shell_ready": 1}, {"exit_code": 0, "output_complete": 1},
    {"exit_code": 0, "cancelled": 1},
])
def test_backend_completion_rejects_ambiguous_or_invalid_outcome(fields):
    with pytest.raises(ValueError):
        TerminalCompletion(**fields)
