"""Pure lifecycle and bounded FIFO reduction for the one session terminal.

Only the existing session writer may commit these observations. It must commit
creation_requested before spawning and dispatched before sending command input.
Replay derives state; it never requests a process or resumes queued input.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .terminal_commands import CommandRef, text_value
from .terminal_session import TerminalAttempt, TerminalClaim, TerminalGeneration, TerminalRetirement
from .terminal_session_events import command_ref, event, validate_event


@dataclass(frozen=True)
class SessionTerminalState:
    claim: TerminalClaim
    phase: str = "claimed"
    attempt: TerminalAttempt | None = None
    generation: TerminalGeneration | None = None
    active_command: CommandRef | None = None
    queued_commands: tuple[CommandRef, ...] = ()
    input_owner: str | None = None
    input_command: CommandRef | None = None
    retirement: TerminalRetirement | None = None
    reason: str | None = None

    def __post_init__(self):
        if not isinstance(self.claim, TerminalClaim):
            raise ValueError("Invalid session terminal claim")
        if self.phase not in ("claimed", "creating", "starting", "paused", "ready",
                              "busy", "busy_paused", "unknown", "retired"):
            raise ValueError("Invalid session terminal phase")
        if self.attempt is not None and (not isinstance(self.attempt, TerminalAttempt)
                                         or self.attempt.ref != self.terminal_ref):
            raise ValueError("Invalid terminal creation attempt")
        if self.generation is not None and (not isinstance(self.generation, TerminalGeneration)
                                            or self.generation.attempt != self.attempt):
            raise ValueError("Invalid terminal generation")
        if ((self.phase == "claimed") != (self.attempt is None)
                or self.phase in ("claimed", "creating") and self.generation is not None
                or self.phase in ("starting", "paused", "ready", "busy", "busy_paused")
                and self.generation is None
                or self.phase in ("busy", "busy_paused") and self.active_command is None
                or self.active_command is not None and (self.generation is None
                    or self.phase not in ("busy", "busy_paused", "unknown", "retired"))):
            raise ValueError("Inconsistent terminal phase and occupancy")
        if (self.phase == "retired") != (self.retirement is not None):
            raise ValueError("Retired terminal requires retirement evidence")
        if self.retirement is not None:
            _check_retirement(self, self.retirement)
        if (not isinstance(self.queued_commands, tuple)
                or len(self.queued_commands) > self.claim.queue_limit
                or self.phase == "claimed" and self.queued_commands):
            raise ValueError("Invalid terminal queue")
        if (self.input_owner not in (None, "model", "user")
                or (self.input_owner is None) != (self.input_command is None)
                or self.input_owner is not None and (
                    self.phase not in ("busy", "busy_paused")
                    or self.input_command != self.active_command)):
            raise ValueError("Invalid terminal input ownership")
        commands = self.queued_commands + ((self.active_command,) if self.active_command is not None else ())
        for command in commands:
            if not isinstance(command, CommandRef):
                raise ValueError("Invalid terminal command reference")
            command_ref(command.record(), self.terminal_ref)
        if len(set(commands)) != len(commands):
            raise ValueError("Terminal queue contains a duplicate command")
        if self.reason is not None:
            text_value(self.reason, limit=1024)

    @property
    def terminal_ref(self):
        return self.claim.ref

    def record(self):
        """A derived query view, never a second durable state authority."""
        return dict(
            contract="sico.terminal.session.state.v1", claim=self.claim.record(), phase=self.phase,
            attempt=self.attempt.record() if self.attempt else None,
            generation=self.generation.record() if self.generation else None,
            active_command=self.active_command.record() if self.active_command else None,
            queued_commands=[command.record() for command in self.queued_commands],
            input_owner=self.input_owner,
            input_command=self.input_command.record() if self.input_command else None,
            retirement=self.retirement.record() if self.retirement else None, reason=self.reason,
        )


def _check_retirement(state, evidence):
    if not isinstance(evidence, TerminalRetirement) or evidence.attempt != state.attempt:
        raise ValueError("Retirement evidence belongs to another creation attempt")
    if (evidence.kind in ("spawn_failed", "owner_stopped") and state.generation is not None
            or evidence.kind == "process_exited" and state.generation is not None
            and evidence.generation != state.generation):
        raise ValueError("Retirement evidence contradicts the observed process")


def _fence(state, value):
    if "generation" in value and TerminalGeneration.from_record(value["generation"]) != state.generation:
        raise ValueError("Stale terminal generation")
    if "attempt" in value and TerminalAttempt.from_record(value["attempt"]) != state.attempt:
        raise ValueError("Stale terminal creation attempt")


def _creation(state, value):
    attempt = TerminalAttempt.from_record(value["attempt"])
    expected = state.attempt.epoch + 1 if state.attempt else 1
    if (state.phase not in ("claimed", "retired") or state.active_command is not None
            or attempt.epoch != expected):
        raise ValueError("Terminal creation is occupied, unresolved, or has a stale epoch")
    if (state.retirement is not None and state.retirement.kind == "host_rebooted"
            and attempt.host_id == state.attempt.host_id
            and attempt.boot_id != state.retirement.new_boot_id):
        raise ValueError("Terminal creation contradicts the verified host boot identity")
    return replace(state, phase="creating", attempt=attempt, generation=None,
                   retirement=None, reason=None)


def _queue(state, value):
    command = command_ref(value["command"], state.terminal_ref)
    kind = value["kind"]
    if kind == "queued":
        if state.phase not in ("ready", "busy"):
            raise ValueError("Terminal shell is not available for queueing")
        if state.active_command == command or command in state.queued_commands:
            raise ValueError("Terminal command was already queued")
        if len(state.queued_commands) >= state.claim.queue_limit:
            raise ValueError("Terminal command queue is full")
        return replace(state, queued_commands=(*state.queued_commands, command))
    if kind == "dispatched":
        if state.phase != "ready" or state.active_command is not None:
            raise ValueError("Terminal already has an active command or is paused")
        if not state.queued_commands or state.queued_commands[0] != command:
            raise ValueError("Terminal commands must dispatch in FIFO order")
        return replace(state, phase="busy", active_command=command,
                       queued_commands=state.queued_commands[1:])
    if kind == "finished":
        if state.phase not in ("busy", "busy_paused", "unknown") or state.active_command != command:
            raise ValueError("Terminal command is not active")
        # A verified command boundary includes shell readiness, not just job exit.
        phase = {"busy": "ready", "busy_paused": "paused", "unknown": "unknown"}[state.phase]
        return replace(state, phase=phase, active_command=None,
                       input_owner=None, input_command=None,
                       reason=state.reason if phase == "unknown" else None)
    if kind == "released":
        if state.phase != "retired" or state.active_command != command:
            raise ValueError("Release requires a retired shell and its active command")
        # This releases occupancy only. The command ledger retains its outcome.
        return replace(state, active_command=None, input_owner=None, input_command=None)
    if command not in state.queued_commands:
        raise ValueError("Terminal command is not queued")
    return replace(state, queued_commands=tuple(c for c in state.queued_commands if c != command))


def _attached(state, value):
    generation = TerminalGeneration.from_record(value["generation"])
    if (generation.attempt != state.attempt
            or state.generation is not None and generation != state.generation):
        raise ValueError("Stale terminal generation")
    if state.phase in ("claimed", "retired"):
        raise ValueError("Attachment requires an unresolved terminal generation")
    # A crash need not leave a durable observation_lost event. Attachment must
    # replay from the last committed phase, not depend on a recovered query view.
    # The owner receipt also proves a usable command-control channel.
    return replace(state, phase="busy_paused" if state.active_command else "paused",
                   generation=generation, reason=None,
                   input_owner=None, input_command=None)


def _input_owner(state, value):
    command = command_ref(value["command"], state.terminal_ref)
    if (state.phase not in ("busy", "busy_paused")
            or state.active_command != command
            or value["owner"] not in ("model", "user")):
        raise ValueError("Terminal input requires the active command")
    return replace(state, input_owner=value["owner"], input_command=command)


def apply_event(state, value):
    """Reduce a committed fact. Callers own trust, deduplication and atomic append.

    The command ledger is the authority for request admission: the owner checks
    it before queueing. This bounded queue does not duplicate completed history.
    Attached/retired facts must originate in verified process-owner evidence.
    """
    ref = validate_event(value)
    kind = value["kind"]
    if state is not None and (not isinstance(state, SessionTerminalState) or state.terminal_ref != ref):
        raise ValueError("Session already owns another terminal")
    if kind == "claimed":
        claim = TerminalClaim.from_record(value["claim"])
        if state is not None and state.claim != claim:
            raise ValueError("Session terminal claim conflict")
        return state if state is not None else SessionTerminalState(claim)
    if state is None:
        raise ValueError("Terminal session has no claim")
    if kind == "creation_requested":
        return _creation(state, value)
    if kind == "started":
        generation = TerminalGeneration.from_record(value["generation"])
        if (state.phase != "creating" or state.generation is not None
                or generation.attempt != state.attempt):
            raise ValueError("Terminal shell was already started or requires reconciliation")
        return replace(state, phase="starting", generation=generation)
    if kind == "attached":
        return _attached(state, value)
    _fence(state, value)
    if kind == "ready":
        if state.phase != "starting":
            raise ValueError("Terminal shell is not starting")
        return replace(state, phase="paused")
    if kind == "resumed":
        if state.phase != "paused":
            raise ValueError("Terminal dispatch is not paused at a verified idle shell")
        return replace(state, phase="ready")
    if kind in ("queued", "dispatched", "finished", "released", "withdrawn"):
        return _queue(state, value)
    if kind == "input_owner":
        return _input_owner(state, value)
    if kind == "observation_lost":
        if state.phase in ("claimed", "retired"):
            raise ValueError("Terminal shell has no unresolved creation")
        return replace(state, phase="unknown", reason=value["reason"],
                       input_owner=None, input_command=None)
    evidence = TerminalRetirement.from_record(value["evidence"])
    if state.phase in ("claimed", "retired"):
        raise ValueError("Terminal shell has no unresolved creation")
    _check_retirement(state, evidence)
    return replace(state, phase="retired", retirement=evidence, reason=evidence.kind,
                   input_owner=None, input_command=None)


def recovered(state):
    """Derive a paused recovery view without pretending the loss was committed."""
    if state is None or state.phase in ("claimed", "retired", "unknown"):
        return state
    return apply_event(state, event("observation_lost", state.terminal_ref,
                                   attempt=state.attempt.record(),
                                   reason="Terminal process owner is not attached"))
