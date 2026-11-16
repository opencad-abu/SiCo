"""Real session journal replay preserves terminal occupancy without execution."""

from copy import deepcopy
from dataclasses import replace

import pytest

from sico.core.contracts import BoundContext, RunState
from sico.core.terminal_commands import CommandRef, CommandRequest
from sico.core.terminal_output import OutputChunk
from sico.core.terminal_session import (
    TerminalAttempt, TerminalClaim, TerminalGeneration, TerminalRef, TerminalRetirement,
)
from sico.core.terminal_session_events import EVENT_CONTRACT, event
from sico.core.terminal_session_state import apply_event, recovered
from sico.core.terminal_state import apply_event as apply_command, event as command_event
from sico.storage.history_reader import SessionReader
from sico.storage.journal import SessionJournal
from sico.storage.terminal_records import JOURNAL_KIND, restore, store


REF = TerminalRef("session", "terminal")
CLAIM = TerminalClaim(REF, "/bin/bash", "/project", "environment")
ATTEMPT = TerminalAttempt(REF, 1, "host", "boot")
GENERATION = TerminalGeneration(ATTEMPT, 123, 456)


def append(journal, state, kind, **fields):
    observation = event(kind, REF, **fields)
    candidate = apply_event(state, observation)
    journal.append(JOURNAL_KIND, store(observation, journal),
                   RunState(BoundContext("instance", "generation", "target", {})))
    return candidate


def start(journal, *, observed=True):
    state = append(journal, None, "claimed", claim=CLAIM.record())
    state = append(journal, state, "creation_requested", attempt=ATTEMPT.record())
    if observed:
        state = append(journal, state, "started", generation=GENERATION.record())
        state = append(journal, state, "ready", generation=GENERATION.record())
        state = append(journal, state, "resumed", generation=GENERATION.record())
    return state


def replay(reader):
    state = None
    for row in reader.stream_events():
        if row["kind"] == JOURNAL_KIND:
            state = apply_event(state, restore(row["payload"], reader))
    return recovered(state)


@pytest.mark.parametrize("observed", [False, True])
def test_creation_reservation_survives_writer_close_and_blocks_second_spawn(tmp_path, observed):
    with SessionJournal(tmp_path, "session") as journal:
        start(journal, observed=observed)
        root = journal.root
    state = replay(SessionReader(root, "session"))
    assert state.phase == "unknown" and state.attempt == ATTEMPT
    assert state.generation == (GENERATION if observed else None)
    with pytest.raises(ValueError, match="occupied"):
        apply_event(state, event("creation_requested", REF, attempt=replace(ATTEMPT, epoch=2).record()))
    other = replace(CLAIM, ref=TerminalRef("session", "second"))
    with pytest.raises(ValueError, match="another terminal"):
        apply_event(state, event("claimed", other.ref, claim=other.record()))


def test_active_command_and_fifo_remain_paused_after_history_reopen(tmp_path):
    first, second = (CommandRef("session", "terminal", name) for name in ("first", "second"))
    with SessionJournal(tmp_path, "session") as journal:
        state = start(journal)
        for command in (first, second):
            state = append(journal, state, "queued", generation=GENERATION.record(),
                           command=command.record())
        append(journal, state, "dispatched", generation=GENERATION.record(), command=first.record())
        root = journal.root
    state = replay(SessionReader(root, "session"))
    assert state.active_command == first and state.queued_commands == (second,)
    with pytest.raises(ValueError, match="paused"):
        apply_event(state, event("dispatched", REF, generation=GENERATION.record(), command=second.record()))
    state = apply_event(state, event("attached", REF, generation=GENERATION.record(),
                                     receipt_id="trusted-owner"))
    state = apply_event(state, event("finished", REF, generation=GENERATION.record(), command=first.record()))
    assert state.phase == "paused" and state.queued_commands == (second,)


@pytest.mark.parametrize("observed", [False, True])
def test_verified_attachment_replays_without_a_committed_loss_event(tmp_path, observed):
    first, second = (CommandRef("session", "terminal", name) for name in ("first", "second"))
    with SessionJournal(tmp_path, "session") as journal:
        state = start(journal, observed=observed)
        if observed:
            for command in (first, second):
                state = append(journal, state, "queued", generation=GENERATION.record(),
                               command=command.record())
            state = append(journal, state, "dispatched", generation=GENERATION.record(),
                           command=first.record())
        root = journal.root
    # A real crash may leave no observation_lost fact in the journal.
    restored = replay(SessionReader(root, "session"))
    with SessionJournal(tmp_path, "session") as journal:
        state = append(journal, restored, "attached", generation=GENERATION.record(),
                       receipt_id="verified-owner")
        assert state.phase == ("busy_paused" if observed else "paused")
        if observed:
            state = append(journal, state, "finished", generation=GENERATION.record(),
                           command=first.record())
        assert state.phase == "paused"
    reader = SessionReader(root, "session")
    durable = None
    for row in reader.stream_events():
        observation = restore(row["payload"], reader)
        assert observation["kind"] != "observation_lost"
        durable = apply_event(durable, observation)
    assert durable == state and durable.generation == GENERATION
    assert durable.queued_commands == ((second,) if observed else ())
    assert replay(SessionReader(root, "session")).phase == "unknown"


def test_committed_retirement_evidence_is_preserved_and_rebuild_is_explicit(tmp_path):
    with SessionJournal(tmp_path, "session") as journal:
        state = start(journal)
        evidence = TerminalRetirement(ATTEMPT, "process_exited", "supervisor-receipt", GENERATION)
        append(journal, state, "retired", evidence=evidence.record())
        root = journal.root
    state = replay(SessionReader(root, "session"))
    assert state.phase == "retired" and state.retirement == evidence
    assert state.attempt.epoch == 1
    # Re-reading the same history did not create an epoch or submit any input.
    assert replay(SessionReader(root, "session")) == state
    with SessionJournal(tmp_path, "session") as journal:
        append(journal, state, "creation_requested", attempt=replace(ATTEMPT, epoch=2).record())
    assert replay(SessionReader(root, "session")).attempt.epoch == 2
    assert replay(SessionReader(root, "session")).phase == "unknown"


def test_failed_append_does_not_publish_candidate_creation_or_release_reservation(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "session") as journal:
        state = append(journal, None, "claimed", claim=CLAIM.record())

        def full(*_args):
            raise OSError("disk full")

        monkeypatch.setattr(journal, "append", full)
        with pytest.raises(OSError, match="disk full"):
            append(journal, state, "creation_requested", attempt=ATTEMPT.record())
        assert state.phase == "claimed" and len(journal.records) == 1
        root = journal.root
    assert replay(SessionReader(root, "session")).phase == "claimed"


def test_session_lifecycle_codec_rejects_foreign_owner_and_leaves_input_unchanged(tmp_path):
    observation = event("creation_requested", REF, attempt=ATTEMPT.record())
    before = deepcopy(observation)
    with SessionJournal(tmp_path, "session") as journal, SessionJournal(tmp_path, "other") as other:
        with pytest.raises(ValueError, match="another session"):
            store(observation, other)
        payload = store(observation, journal)
        assert restore(payload, journal) == observation == before
        with pytest.raises(ValueError, match="another session"):
            restore(payload, other)
        payload["attempt"]["ref"]["terminal_id"] = "foreign"
        with pytest.raises(ValueError, match="identity conflict"):
            restore(payload, journal)


def test_command_bytes_and_terminal_lifecycle_share_one_session_journal(tmp_path):
    request = CommandRequest(CommandRef("session", "terminal", "command"), "request", "/project",
                             "environment", mode="pty", shell="/bin/bash", text="pwd")
    observations = [
        command_event("accepted", request.ref, request=request.record()),
        command_event("started", request.ref, execution_id="execution"),
        command_event("output", request.ref,
                      chunk=OutputChunk(request.ref, 1, 0, "pty", b"/project\n").record()),
        command_event("exited", request.ref, exit_code=0, signal=None, output_complete=True,
                      output_sequence=1, output_bytes=9),
    ]
    with SessionJournal(tmp_path, "session") as journal:
        state = start(journal)
        journal.append(JOURNAL_KIND, store(observations[0], journal),
                       RunState(BoundContext("instance", "generation", "target", {})))
        state = append(journal, state, "queued", generation=GENERATION.record(), command=request.ref.record())
        state = append(journal, state, "dispatched", generation=GENERATION.record(), command=request.ref.record())
        for observation in observations[1:]:
            journal.append(JOURNAL_KIND, store(observation, journal),
                           RunState(BoundContext("instance", "generation", "target", {})))
        append(journal, state, "finished", generation=GENERATION.record(), command=request.ref.record())
        root = journal.root
    terminal, command = None, None
    data = bytearray()
    reader = SessionReader(root, "session")
    for row in reader.stream_events():
        observation = restore(row["payload"], reader)
        if observation["contract"] == EVENT_CONTRACT:
            terminal = apply_event(terminal, observation)
        else:
            command = apply_command(command, observation)
            if observation["kind"] == "output":
                data.extend(OutputChunk.from_record(observation["chunk"]).data)
    assert recovered(terminal).phase == "unknown" and terminal.active_command is None
    assert command.succeeded and command.output_complete and data == b"/project\n"
