"""Singleton/FIFO contracts; these tests do not launch a product shell."""

from dataclasses import replace

import pytest

from sico.core.terminal_commands import CommandRef, CommandRequest
from sico.core.terminal_session import (
    TerminalAttempt, TerminalClaim, TerminalGeneration, TerminalRef, TerminalRetirement,
)
from sico.core.terminal_session_events import event, validate_event
from sico.core.terminal_session_state import SessionTerminalState, apply_event, recovered


REF = TerminalRef("session", "terminal")
CLAIM = TerminalClaim(REF, "/bin/bash", "/project", "environment")
ATTEMPT = TerminalAttempt(REF, 1, "host", "boot")
GENERATION = TerminalGeneration(ATTEMPT, 123, 456)


def fact(kind, **fields):
    return event(kind, REF, **fields)


def step(state, kind, **fields):
    if "generation" not in fields:
        fields["generation"] = state.generation.record()
    return apply_event(state, fact(kind, **fields))


def starting_state():
    state = apply_event(None, fact("claimed", claim=CLAIM.record()))
    state = apply_event(state, fact("creation_requested", attempt=ATTEMPT.record()))
    return apply_event(state, fact("started", generation=GENERATION.record()))


def ready_state(queue_limit=64):
    state = starting_state()
    if queue_limit != 64:
        state = replace(state, claim=replace(CLAIM, queue_limit=queue_limit))
    state = step(state, "ready")
    return step(state, "resumed")


def command(name):
    return CommandRef("session", "terminal", name)


def retired(state, kind="process_exited"):
    evidence = TerminalRetirement(state.attempt, kind, "receipt",
                                  state.generation if kind == "process_exited" else None,
                                  "new-boot" if kind == "host_rebooted" else None)
    return apply_event(state, fact("retired", evidence=evidence.record()))


def test_claim_is_unique_and_identical_redelivery_preserves_state():
    state = ready_state()
    assert apply_event(state, fact("claimed", claim=CLAIM.record())) is state
    other = replace(CLAIM, ref=TerminalRef("session", "another"))
    with pytest.raises(ValueError, match="another terminal"):
        apply_event(state, event("claimed", other.ref, claim=other.record()))
    with pytest.raises(ValueError, match="claim conflict"):
        apply_event(state, fact("claimed", claim=replace(CLAIM, cwd="/other").record()))


@pytest.mark.parametrize("phase", ["creating", "starting", "paused", "ready", "busy"])
def test_recovered_reservations_block_another_creation_even_without_start_record(phase):
    state = starting_state()
    if phase == "creating":
        state = replace(state, phase=phase, generation=None)
    elif phase != "starting":
        state = step(state, "ready")
        if phase != "paused":
            state = step(state, "resumed")
        if phase == "busy":
            state = step(state, "queued", command=command("first").record())
            state = step(state, "dispatched", command=command("first").record())
    restored = recovered(state)
    assert restored.phase == "unknown" and restored.attempt == ATTEMPT
    assert restored.active_command == state.active_command
    assert restored.generation == state.generation
    with pytest.raises(ValueError, match="occupied"):
        apply_event(restored, fact("creation_requested", attempt=replace(ATTEMPT, epoch=2).record()))
    with pytest.raises(ValueError, match="reconciliation"):
        apply_event(restored, fact("started", generation=GENERATION.record()))


def test_fifo_keeps_the_observed_generation_and_allows_only_one_active_command():
    state = ready_state()
    first, second = command("first"), command("second")
    for item in (first, second):
        state = step(state, "queued", command=item.record())
    with pytest.raises(ValueError, match="FIFO"):
        step(state, "dispatched", command=second.record())
    state = step(state, "dispatched", command=first.record())
    with pytest.raises(ValueError, match="active command"):
        step(state, "dispatched", command=second.record())
    state = step(state, "finished", command=first.record())
    state = step(state, "dispatched", command=second.record())
    assert state.generation == GENERATION and state.active_command == second


def test_queue_budget_and_pending_duplicate_rejection():
    state = ready_state(queue_limit=1)
    first = command("first")
    state = step(state, "queued", command=first.record())
    with pytest.raises(ValueError, match="full"):
        step(state, "queued", command=command("second").record())
    with pytest.raises(ValueError, match="already queued"):
        step(state, "queued", command=first.record())
    state = step(state, "dispatched", command=first.record())
    with pytest.raises(ValueError, match="already queued"):
        step(state, "queued", command=first.record())


def test_reattachment_preserves_pending_work_and_does_not_resume_dispatch():
    state = ready_state()
    for item in (command("first"), command("second")):
        state = step(state, "queued", command=item.record())
    state = step(state, "dispatched", command=command("first").record())
    state = recovered(state)
    state = step(state, "attached", receipt_id="verified-owner")
    assert state.phase == "busy_paused"
    state = step(state, "finished", command=command("first").record())
    assert state.phase == "paused" and state.queued_commands == (command("second"),)
    with pytest.raises(ValueError, match="paused"):
        step(state, "dispatched", command=command("second").record())
    state = step(state, "resumed")
    assert step(state, "dispatched", command=command("second").record()).phase == "busy"


def test_verified_attachment_can_resolve_the_creation_start_record_gap():
    state = recovered(replace(starting_state(), phase="creating", generation=None))
    state = step(state, "attached", generation=GENERATION.record(), receipt_id="owner-receipt")
    assert state.generation == GENERATION and state.phase == "paused"


def test_late_completion_does_not_restore_the_lost_process_owner():
    state = step(ready_state(), "queued", command=command("first").record())
    state = step(state, "dispatched", command=command("first").record())
    state = step(recovered(state), "finished", command=command("first").record())
    assert state.phase == "unknown" and state.active_command is None
    with pytest.raises(ValueError, match="not paused"):
        step(state, "resumed")
    assert step(state, "attached", receipt_id="verified-owner").phase == "paused"


def test_retirement_and_explicit_release_preserve_queue_across_rebuild():
    state = ready_state()
    for item in (command("first"), command("second")):
        state = step(state, "queued", command=item.record())
    state = step(state, "dispatched", command=command("first").record())
    state = retired(recovered(state))
    attempt = replace(ATTEMPT, epoch=2)
    with pytest.raises(ValueError, match="occupied"):
        apply_event(state, fact("creation_requested", attempt=attempt.record()))
    state = apply_event(state, fact("released", attempt=ATTEMPT.record(),
                                    command=command("first").record(), reason="Outcome unknown"))
    state = apply_event(state, fact("creation_requested", attempt=attempt.record()))
    assert state.queued_commands == (command("second"),)
    generation = replace(GENERATION, attempt=attempt, pid=124)
    state = step(state, "started", generation=generation.record())
    state = step(state, "ready")
    assert state.phase == "paused"
    with pytest.raises(ValueError, match="paused"):
        step(state, "dispatched", command=command("second").record())


@pytest.mark.parametrize("kind", ["ready", "resumed", "attached", "queued", "dispatched",
                                  "finished", "observation_lost", "retired", "released", "withdrawn"])
def test_old_generation_events_cannot_mutate_a_new_shell(kind):
    state = retired(ready_state())
    attempt = replace(ATTEMPT, epoch=2)
    state = apply_event(state, fact("creation_requested", attempt=attempt.record()))
    state = step(state, "started", generation=replace(GENERATION, attempt=attempt).record())
    fields = {"generation": GENERATION.record()}
    if kind == "attached":
        fields["receipt_id"] = "receipt"
    elif kind == "retired":
        fields = {"evidence": TerminalRetirement(ATTEMPT, "process_exited", "receipt",
                                                GENERATION).record()}
    elif kind in ("observation_lost", "released", "withdrawn"):
        fields = {"attempt": ATTEMPT.record(), "reason": "stale"}
    if kind in ("queued", "dispatched", "finished", "released", "withdrawn"):
        fields["command"] = command("first").record()
    with pytest.raises(ValueError, match="Stale|another creation attempt"):
        apply_event(state, fact(kind, **fields))


def test_retirement_requires_structured_noncontradictory_owner_evidence():
    state = ready_state()
    with pytest.raises(ValueError):
        event("retired", REF, evidence={"reason": "I checked"})
    with pytest.raises(ValueError, match="contradicts"):
        retired(state, "spawn_failed")
    with pytest.raises(ValueError, match="different boot"):
        TerminalRetirement(ATTEMPT, "host_rebooted", "receipt", new_boot_id="boot")
    with pytest.raises(ValueError, match="contradicts"):
        apply_event(state, fact("retired", evidence=TerminalRetirement(
            ATTEMPT, "process_exited", "receipt", replace(GENERATION, process_start=457),
        ).record()))
    assert retired(recovered(state), "host_rebooted").phase == "retired"
    unstarted = recovered(replace(starting_state(), phase="creating", generation=None))
    assert retired(unstarted, "spawn_failed").phase == "retired"


def test_rebuild_on_the_rebooted_host_requires_the_observed_new_boot_identity():
    state = retired(ready_state(), "host_rebooted")
    with pytest.raises(ValueError, match="boot identity"):
        apply_event(state, fact("creation_requested", attempt=replace(ATTEMPT, epoch=2).record()))
    state = apply_event(state, fact("creation_requested",
                                    attempt=replace(ATTEMPT, epoch=2, boot_id="new-boot").record()))
    assert state.phase == "creating" and state.attempt.boot_id == "new-boot"


def test_request_can_preserve_current_shell_directory_and_explicit_changes_are_distinct():
    request = CommandRequest(command("first"), "request", None, "environment",
                             mode="pty", shell="/bin/bash", text="pwd")
    assert CommandRequest.from_record(request.record()).cwd is None
    assert replace(request, cwd="/next").cwd == "/next"
    with pytest.raises(ValueError, match="absolute"):
        replace(request, cwd="relative")


@pytest.mark.parametrize("kind", [None, [], {}, 1, "unsupported"])
def test_malformed_event_kind_is_a_value_error(kind):
    with pytest.raises(ValueError):
        validate_event(dict(contract="sico.terminal.session.event.v1", kind=kind, ref=REF.record()))


@pytest.mark.parametrize("fields", [dict(phase="ready"), dict(phase="creating"),
    dict(generation={}), dict(active_command=command("first")), dict(queued_commands=[]),
    dict(attempt=ATTEMPT), dict(retirement="text"),
    dict(phase="unknown", attempt=ATTEMPT, generation=GENERATION, active_command={})])
def test_invalid_derived_state_is_rejected(fields):
    with pytest.raises(ValueError):
        SessionTerminalState(CLAIM, **fields)


def test_foreign_command_and_reused_epoch_are_rejected():
    with pytest.raises(ValueError, match="another terminal"):
        step(ready_state(), "queued", command=CommandRef("other", "terminal", "first").record())
    state = retired(ready_state())
    with pytest.raises(ValueError, match="stale epoch"):
        apply_event(state, fact("creation_requested", attempt=ATTEMPT.record()))
    assert state.record()["contract"] == "sico.terminal.session.state.v1"
