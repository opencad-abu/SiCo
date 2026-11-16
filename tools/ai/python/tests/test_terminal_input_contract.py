"""Bounded PTY stdin ownership and non-secret input observations."""

from dataclasses import replace

import pytest

from sico.core.terminal_commands import CommandRef, CommandRequest
from sico.core.terminal_input import MAX_INPUT_BYTES, TerminalInput
from sico.core.terminal_session import (
    TerminalAttempt, TerminalClaim, TerminalGeneration, TerminalRef,
)
from sico.core.terminal_session_events import event as session_event
from sico.core.terminal_session_state import apply_event as apply_session
from sico.core.terminal_state import apply_event as apply_command, event as command_event


REF = TerminalRef("session", "terminal")
COMMAND = CommandRef("session", "terminal", "command")
CLAIM = TerminalClaim(REF, "/bin/bash", "/project", "environment", mode="pty")
ATTEMPT = TerminalAttempt(REF, 1, "host", "boot")
GENERATION = TerminalGeneration(ATTEMPT, 123, 456)


def accepted():
    request = CommandRequest(COMMAND, "request", None, "environment", mode="pty",
                             shell="/bin/bash", text="cat")
    return apply_command(None, command_event("accepted", COMMAND, request=request.record()))


def running():
    return apply_command(accepted(), command_event("started", COMMAND, execution_id="execution"))


def terminal_state():
    state = apply_session(None, session_event("claimed", REF, claim=CLAIM.record()))
    state = apply_session(state, session_event("creation_requested", REF,
                                               attempt=ATTEMPT.record()))
    state = apply_session(state, session_event("started", REF,
                                               generation=GENERATION.record()))
    state = apply_session(state, session_event("ready", REF,
                                               generation=GENERATION.record()))
    state = apply_session(state, session_event("resumed", REF,
                                               generation=GENERATION.record()))
    state = apply_session(state, session_event("queued", REF,
        generation=GENERATION.record(), command=COMMAND.record()))
    return apply_session(state, session_event("dispatched", REF,
        generation=GENERATION.record(), command=COMMAND.record()))


def test_input_round_trip_is_bounded_and_noncanonical_bytes_are_rejected():
    value = TerminalInput(COMMAND, "user", 1, b"hello\n")
    record = value.record()
    assert TerminalInput.from_record(record) == value
    with pytest.raises(ValueError, match="size"):
        TerminalInput(COMMAND, "user", 1, b"")
    with pytest.raises(ValueError, match="size"):
        TerminalInput(COMMAND, "user", 1, b"x" * (MAX_INPUT_BYTES + 1))
    with pytest.raises(ValueError, match="owner"):
        TerminalInput(COMMAND, "system", 1, b"x")
    with pytest.raises(ValueError, match="encoding"):
        TerminalInput.from_record({**record, "data": record["data"] + "="})


def test_input_is_accepted_only_while_the_foreground_command_runs():
    state = running()
    state = apply_command(state, command_event("input", COMMAND, owner="user", size=4))
    assert (state.input_chunks, state.input_bytes) == (1, 4)
    cancelled = apply_command(state, command_event("cancel_requested", COMMAND))
    with pytest.raises(ValueError, match="foreground"):
        apply_command(cancelled, command_event("input", COMMAND, owner="user", size=1))
    exited = apply_command(state, command_event(
        "exited", COMMAND, exit_code=0, signal=None, output_complete=True,
        output_sequence=0, output_bytes=0))
    with pytest.raises(ValueError, match="foreground"):
        apply_command(exited, command_event("input", COMMAND, owner="user", size=1))


def test_owner_takeover_is_explicit_and_finish_releases_it():
    state = terminal_state()
    with pytest.raises(ValueError, match="input owner"):
        apply_session(state, session_event("input_owner", REF,
            generation=GENERATION.record(), command=COMMAND.record(), owner="other"))
    state = apply_session(state, session_event("input_owner", REF,
        generation=GENERATION.record(), command=COMMAND.record(), owner="model"))
    assert state.input_owner == "model" and state.input_command == COMMAND
    state = apply_session(state, session_event("input_owner", REF,
        generation=GENERATION.record(), command=COMMAND.record(), owner="user"))
    assert state.input_owner == "user"
    state = apply_session(state, session_event("finished", REF,
        generation=GENERATION.record(), command=COMMAND.record()))
    assert state.input_owner is None and state.input_command is None


def test_owner_takeover_requires_the_active_command():
    state = terminal_state()
    other = CommandRef("session", "terminal", "other")
    with pytest.raises(ValueError, match="active command"):
        apply_session(state, session_event("input_owner", REF,
            generation=GENERATION.record(), command=other.record(), owner="user"))


def test_input_ownership_is_fenced_to_the_observed_generation():
    state = terminal_state()
    with pytest.raises(ValueError, match="Stale"):
        apply_session(state, session_event("input_owner", REF,
            generation=replace(GENERATION, process_start=999).record(),
            command=COMMAND.record(), owner="user"))
