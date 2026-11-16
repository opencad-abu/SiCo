"""Dispatcher ownership gates stdin without persisting its bytes."""

import pytest

from session_terminal_fixtures import ControlledBackend, gated, observations, request, terminal
from sico.core.terminal_input import TerminalInput


def test_explicit_owner_controls_stdin_and_input_bytes_are_not_journaled(terminal):
    with terminal(ControlledBackend(run=gated)) as (dispatch, backend, journal):
        command = request()
        dispatch.submit(command)
        assert backend.entered.wait(2)
        with pytest.raises(ValueError, match="owned"):
            dispatch.write_input(command.ref, "model", b"secret\\n")
        dispatch.take_input(command.ref, "user")
        dispatch.write_input(command.ref, "user", b"hello\\n")
        with pytest.raises(ValueError, match="owned"):
            dispatch.write_input(command.ref, "model", b"other\\n")
        assert backend.inputs == [TerminalInput(command.ref, "user", 1, b"hello\\n")]
        backend.release.set()
        assert dispatch.wait(command.ref, 2).succeeded
        facts = observations(journal)
        assert [fact["kind"] for fact in facts if fact.get("ref") == command.ref.record()] == [
            "accepted", "started", "input", "output", "exited"]
        encoded = repr(facts).encode()
        assert b"hello" not in encoded and b"secret" not in encoded
        assert dispatch.snapshot()["terminal"]["input_owner"] is None


def test_input_requires_running_command_and_preserves_the_existing_counter(terminal):
    with terminal(ControlledBackend(run=gated)) as (dispatch, backend, _):
        command = request()
        dispatch.submit(command)
        assert backend.entered.wait(2)
        dispatch.take_input(command.ref, "user")
        for sequence, data in enumerate((b"a", b"bc", b"def"), 1):
            dispatch.write_input(command.ref, "user", data)
            assert backend.inputs[-1].sequence == sequence
        backend.release.set()
        assert dispatch.wait(command.ref, 2).succeeded
        with pytest.raises(ValueError, match="owned"):
            dispatch.write_input(command.ref, "user", b"late")
