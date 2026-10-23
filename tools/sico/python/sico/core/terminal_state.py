"""Pure reduction of terminal observations; history can never start a process."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .contracts import identifier, json_copy
from .terminal_commands import CommandRef, CommandRequest, exact_fields, text_value
from .terminal_output import OutputChunk, output_position, position
from .terminal_input import INPUT_OWNERS, MAX_INPUT_BYTES
from .terminal_session import TerminalGeneration

EVENT_CONTRACT = "sico.terminal.event.v1"
FIELDS = {
    "accepted": ("request",),
    "started": ("execution_id",),
    "execution_context": ("cwd", "generation"),
    "output": ("chunk",),
    "input": ("owner", "size"),
    "cancel_requested": (),
    "exited": ("exit_code", "signal", "output_complete", "output_sequence", "output_bytes"),
    "not_started": ("reason",),
    "observation_lost": ("reason",),
}


@dataclass(frozen=True)
class CommandState:
    request: CommandRequest
    phase: str = "accepted"
    execution_id: str | None = None
    cancel_requested: bool = False
    output_sequence: int = 0
    output_bytes: int = 0
    input_chunks: int = 0
    input_bytes: int = 0
    output_complete: bool = False
    exit_code: int | None = None
    signal: int | None = None
    reason: str | None = None
    execution_cwd: str | None = None
    generation: TerminalGeneration | None = None

    @property
    def succeeded(self):
        """Command success is independent of output completeness."""
        return self.phase == "exited" and self.exit_code == 0

    def record(self):
        """Return a derived view for a query; it is never an execution command."""
        return {
            "contract": "sico.terminal.state.v1",
            "request": self.request.record(),
            "phase": self.phase,
            "execution_id": self.execution_id,
            "cancel_requested": self.cancel_requested,
            "output_sequence": self.output_sequence,
            "output_bytes": self.output_bytes,
            "input_chunks": self.input_chunks,
            "input_bytes": self.input_bytes,
            "output_complete": self.output_complete,
            "exit_code": self.exit_code,
            "signal": self.signal,
            "reason": self.reason,
            "execution_cwd": self.execution_cwd,
            "generation": self.generation.record() if self.generation else None,
        }


def event(kind, ref, **fields):
    """Build a detached JSON observation; apply_event validates its semantics."""
    if not isinstance(kind, str) or kind not in FIELDS or not isinstance(ref, CommandRef):
        raise ValueError("Invalid terminal event")
    exact_fields(fields, FIELDS[kind])
    return dict(contract=EVENT_CONTRACT, kind=kind, ref=ref.record(), **json_copy(fields))


def validate_event(value):
    """Validate one observation independently of its place in command history."""
    if (not isinstance(value, dict) or not isinstance(value.get("kind"), str)
            or value["kind"] not in FIELDS):
        raise ValueError("Invalid terminal event kind")
    kind = value["kind"]
    exact_fields(value, ("contract", "kind", "ref", *FIELDS[kind]))
    if value["contract"] != EVENT_CONTRACT:
        raise ValueError("Unsupported terminal event contract")
    ref = CommandRef.from_record(value["ref"])
    if kind == "accepted":
        if CommandRequest.from_record(value["request"]).ref != ref:
            raise ValueError("Terminal admission identity conflict")
    elif kind == "started":
        identifier(value["execution_id"])
    elif kind == "execution_context":
        text_value(value["cwd"], limit=4096)
        generation = TerminalGeneration.from_record(value["generation"])
        if (not value["cwd"].startswith("/") or generation.attempt.ref.session_id != ref.session_id
                or generation.attempt.ref.terminal_id != ref.terminal_id):
            raise ValueError("Execution context does not match the command")
    elif kind == "output":
        if OutputChunk.from_record(value["chunk"]).ref != ref:
            raise ValueError("Terminal output belongs to another command")
    elif kind == "input":
        if (value["owner"] not in INPUT_OWNERS
                or type(value["size"]) is not int
                or not 1 <= value["size"] <= MAX_INPUT_BYTES):
            raise ValueError("Invalid terminal input observation")
    elif kind in ("not_started", "observation_lost"):
        text_value(value["reason"], limit=1024)
    elif kind == "exited":
        _exit_values(value)
    return ref


def _output(state, value):
    chunk = OutputChunk.from_record(value["chunk"])
    return advance_output(state, chunk.ref, chunk.sequence, chunk.offset, chunk.stream, len(chunk.data))


def _input(state, value):
    if state.phase != "running" or state.cancel_requested:
        raise ValueError("Terminal input arrived after the foreground command")
    return replace(state, input_chunks=state.input_chunks + 1,
                   input_bytes=state.input_bytes + value["size"])


def advance_output(state, ref, sequence, offset, stream, size):
    """Reduce capture metadata; this does not certify readable attachment bytes."""
    end = output_position(ref, sequence, offset, stream, size)
    if not isinstance(state, CommandState):
        raise ValueError("Output has no accepted request")
    if state.phase not in ("running", "unknown") or state.execution_id is None:
        raise ValueError("Output has no observed execution")
    streams = ("pty",) if state.request.mode == "pty" else ("stdout", "stderr")
    if (ref != state.request.ref or stream not in streams
            or sequence != state.output_sequence + 1 or offset != state.output_bytes):
        raise ValueError("Terminal output identity, order or stream mismatch")
    return replace(state, output_sequence=sequence, output_bytes=end)


def _exit_values(value):
    code, signal = value["exit_code"], value["signal"]
    if ((code is None) == (signal is None)
            or code is not None and (type(code) is not int or not 0 <= code <= 255)
            or signal is not None and (type(signal) is not int or not 1 <= signal <= 127)):
        raise ValueError("Exit requires exactly one exit code or signal")
    position(value["output_sequence"])
    position(value["output_bytes"])
    if type(value["output_complete"]) is not bool:
        raise ValueError("Invalid terminal output completeness")


def _exit(state, value):
    if state.phase not in ("running", "unknown") or state.execution_id is None:
        raise ValueError("Exit has no observed execution")
    if (value["output_sequence"] != state.output_sequence
            or value["output_bytes"] != state.output_bytes):
        raise ValueError("Exit output watermark does not match committed observations")
    return replace(state, phase="exited", exit_code=value["exit_code"], signal=value["signal"],
                   output_complete=value["output_complete"], reason=None)


def apply_event(state, value):
    """Reduce one committed observation, in order, without I/O or process handles.

    Journal sequence ownership removes duplicate deliveries. A duplicate admission
    with the exact request is harmless; changed content for that identity fails.
    """
    ref = validate_event(value)
    kind = value["kind"]
    if state is not None and (not isinstance(state, CommandState) or state.request.ref != ref):
        raise ValueError("Terminal event belongs to another command")
    if kind == "accepted":
        request = CommandRequest.from_record(value["request"])
        if request.ref != ref or state is not None and state.request != request:
            raise ValueError("Terminal admission identity conflict")
        return state if state is not None else CommandState(request)
    if state is None:
        raise ValueError("Terminal event has no accepted request")
    if kind == "execution_context":
        if state.phase != "accepted" or state.execution_cwd is not None:
            raise ValueError("Execution context was already recorded or command started")
        return replace(state, execution_cwd=value["cwd"],
                       generation=TerminalGeneration.from_record(value["generation"]))
    if kind == "started":
        execution_id = identifier(value["execution_id"])
        if state.phase != "accepted":
            raise ValueError("Terminal execution was already observed or requires reconciliation")
        return replace(state, phase="running", execution_id=execution_id)
    if kind == "output":
        return _output(state, value)
    if kind == "input":
        return _input(state, value)
    if kind == "exited":
        return _exit(state, value)
    if kind == "not_started":
        if state.phase != "accepted":
            raise ValueError("Cannot prove that an observed or uncertain execution did not start")
        return replace(state, phase="not_started", reason=value["reason"], output_complete=True)
    if kind == "observation_lost":
        if state.phase not in ("accepted", "running", "unknown"):
            raise ValueError("Terminal execution already ended")
        return replace(state, phase="unknown", reason=value["reason"], output_complete=False)
    if state.phase not in ("accepted", "running", "unknown"):
        raise ValueError("Terminal execution already ended")
    return replace(state, cancel_requested=True)


def recovered(state):
    """Reopen observations only; live execution must be reconciled separately."""
    if state.phase in ("accepted", "running"):
        return apply_event(state, event("observation_lost", state.request.ref,
                                       reason="Execution owner is not attached"))
    return state
