"""Versioned observations of the single session terminal, without I/O."""

from .contracts import identifier, json_copy
from .terminal_commands import CommandRef, exact_fields, text_value
from .terminal_session import (
    TerminalAttempt, TerminalClaim, TerminalGeneration, TerminalRef, TerminalRetirement,
)

EVENT_CONTRACT = "sico.terminal.session.event.v1"
EVENT_FIELDS = {
    "claimed": ("claim",),
    "creation_requested": ("attempt",),
    "started": ("generation",),
    "ready": ("generation",),
    "resumed": ("generation",),
    "attached": ("generation", "receipt_id"),
    "queued": ("generation", "command"),
    "dispatched": ("generation", "command"),
    "input_owner": ("generation", "command", "owner"),
    "finished": ("generation", "command"),
    "observation_lost": ("attempt", "reason"),
    "retired": ("evidence",),
    "released": ("attempt", "command", "reason"),
    "withdrawn": ("attempt", "command", "reason"),
}


def command_ref(value, ref):
    command = CommandRef.from_record(value)
    if (command.session_id, command.terminal_id) != (ref.session_id, ref.terminal_id):
        raise ValueError("Terminal command belongs to another terminal")
    return command


def event(kind, ref, **fields):
    """Build a detached observation; validate_event also checks nested values."""
    if not isinstance(kind, str) or kind not in EVENT_FIELDS or not isinstance(ref, TerminalRef):
        raise ValueError("Invalid terminal session event")
    exact_fields(fields, EVENT_FIELDS[kind])
    value = dict(contract=EVENT_CONTRACT, kind=kind, ref=ref.record(), **json_copy(fields))
    validate_event(value)
    return value


def validate_event(value):
    if (not isinstance(value, dict) or value.get("contract") != EVENT_CONTRACT
            or not isinstance(value.get("kind"), str) or value["kind"] not in EVENT_FIELDS):
        raise ValueError("Invalid terminal session event")
    kind = value["kind"]
    exact_fields(value, ("contract", "kind", "ref", *EVENT_FIELDS[kind]))
    ref = TerminalRef.from_record(value["ref"])
    if "claim" in value and TerminalClaim.from_record(value["claim"]).ref != ref:
        raise ValueError("Terminal claim identity conflict")
    if "attempt" in value and TerminalAttempt.from_record(value["attempt"]).ref != ref:
        raise ValueError("Terminal attempt identity conflict")
    if ("generation" in value
            and TerminalGeneration.from_record(value["generation"]).attempt.ref != ref):
        raise ValueError("Terminal generation identity conflict")
    if "evidence" in value and TerminalRetirement.from_record(value["evidence"]).attempt.ref != ref:
        raise ValueError("Terminal retirement identity conflict")
    if "command" in value:
        command_ref(value["command"], ref)
    if "owner" in value and value["owner"] not in ("model", "user"):
        raise ValueError("Invalid terminal input owner")
    if "reason" in value:
        text_value(value["reason"], limit=1024)
    if "receipt_id" in value:
        identifier(value["receipt_id"])
    return ref
