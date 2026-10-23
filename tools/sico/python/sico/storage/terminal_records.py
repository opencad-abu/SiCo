"""Terminal observation codec for the existing session journal and attachments.

The session owner validates the transition before ``store``, appends its returned
payload, then publishes the derived state only after the journal append succeeds.
This module neither owns a journal writer nor starts or reconnects a process.
"""

from __future__ import annotations

import json

from cadai.json_values import strict_json

from ..core.contracts import json_copy
from ..core.terminal_commands import CommandRef, exact_fields
from ..core.terminal_output import MAX_CHUNK_BYTES, OutputChunk, output_position
from ..core.terminal_session import TerminalRef
from ..core.terminal_session_events import (
    EVENT_CONTRACT as SESSION_EVENT_CONTRACT, validate_event as validate_session_event,
)
from ..core.terminal_state import EVENT_CONTRACT, validate_event
from .artifact_reference import validate as _artifact_reference

JOURNAL_CONTRACT = "sico.terminal.journal.v1"
SESSION_JOURNAL_CONTRACT = "sico.terminal.session.journal.v1"
JOURNAL_KIND = "terminal.observed"
MAX_OUTPUT_ARTIFACT = 2 * MAX_CHUNK_BYTES


def _owned(ref, session_id):
    if ref.session_id != session_id:
        raise ValueError("Terminal observation belongs to another session")


def _output_metadata(chunk):
    return dict(sequence=chunk.sequence, offset=chunk.offset, stream=chunk.stream,
                size=len(chunk.data))


def store(observation, journal):
    """Persist output first, returning a detached journal payload without bytes.

    Only the existing owning SessionJournal writes the artifact. If the later
    event append fails, the unreferenced attachment is never committed output.
    This codec checks event shape and ownership, not the caller's command state.
    """
    session_event = (isinstance(observation, dict)
                     and observation.get("contract") == SESSION_EVENT_CONTRACT)
    ref = validate_session_event(observation) if session_event else validate_event(observation)
    _owned(ref, journal.session_id)
    value = json_copy(observation)
    if value["kind"] == "output":
        chunk = OutputChunk.from_record(value["chunk"])
        data = json.dumps(chunk.record(), ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
        artifact = journal.artifact(data)
        value["chunk"] = dict(_output_metadata(chunk), artifact=artifact)
    value["contract"] = SESSION_JOURNAL_CONTRACT if session_event else JOURNAL_CONTRACT
    return value


def validate_metadata(payload, session_id):
    """Validate persisted coordinates without opening or certifying attachments."""
    if (not isinstance(payload, dict)
            or payload.get("contract") not in (JOURNAL_CONTRACT, SESSION_JOURNAL_CONTRACT)):
        raise ValueError("Unsupported terminal journal contract")
    value = json_copy(payload)
    if value["contract"] == SESSION_JOURNAL_CONTRACT:
        ref = TerminalRef.from_record(value.get("ref"))
        _owned(ref, session_id)
        value["contract"] = SESSION_EVENT_CONTRACT
        validate_session_event(value)
        return ref
    ref = CommandRef.from_record(value.get("ref"))
    _owned(ref, session_id)
    value["contract"] = EVENT_CONTRACT
    if value.get("kind") == "output":
        exact_fields(value, ("contract", "kind", "ref", "chunk"))
        metadata = value.get("chunk")
        exact_fields(metadata, ("sequence", "offset", "stream", "size", "artifact"))
        output_position(ref, metadata["sequence"], metadata["offset"], metadata["stream"], metadata["size"])
        artifact = metadata["artifact"]
        exact_fields(artifact, ("path", "sha256", "size", "complete"))
        if (not isinstance(artifact["path"], str) or not isinstance(artifact["sha256"], str)
                or artifact["complete"] is not True or type(artifact["size"]) is not int
                or not 1 <= artifact["size"] <= MAX_OUTPUT_ARTIFACT):
            raise ValueError("Invalid terminal output artifact size or completeness")
        _artifact_reference(artifact["path"], artifact["sha256"], session_id)
    else:
        validate_event(value)
    return ref


def restore(payload, reader):
    """Read one observation, verifying any referenced attachment bytes and identity."""
    ref = validate_metadata(payload, reader.session_id)
    value = json_copy(payload)
    if value["contract"] == SESSION_JOURNAL_CONTRACT:
        value["contract"] = SESSION_EVENT_CONTRACT
        return value
    value["contract"] = EVENT_CONTRACT
    if value["kind"] == "output":
        metadata = value["chunk"]
        artifact = metadata["artifact"]
        text = reader.artifact_text(artifact["path"], artifact["sha256"],
                                    max_bytes=MAX_OUTPUT_ARTIFACT)
        if len(text.encode("utf-8")) != artifact["size"]:
            raise ValueError("Terminal output artifact size mismatch")
        chunk = OutputChunk.from_record(strict_json(text))
        expected = _output_metadata(chunk)
        if (chunk.ref != ref or any(type(metadata[key]) is not type(item)
                                   or metadata[key] != item for key, item in expected.items())):
            raise ValueError("Terminal output artifact identity or position mismatch")
        value["chunk"] = chunk.record()
    validate_event(value)
    return value
