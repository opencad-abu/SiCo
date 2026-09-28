"""Bounded reads of durable input evidence; never rebuild or execute a queue."""

import errno
import math
import os
from pathlib import Path

from ..core.contracts import BoundContext, identifier
from ..transport.framing import ProtocolError, strict_json
from .history import owned_directory
from .journal import open_private, sync_directory
from .roots import agent_root

MAX_INPUT_RECORD = 262144
INPUT_STATES = {"queued", "executing", "waiting_user", "completed", "failed", "cancelled",
                "needs_reconcile", "not_started"}


def inbox_directory(project, session_id):
    identifier(session_id)
    return agent_root(project) / "sessions" / session_id / "inbox"


def read_input(directory, input_id, *, confirm=False):
    """An existing atomic file is evidence, including after an uncertain directory sync.

    Acceptance queries confirm both the file and its name before acknowledging it.
    They never enqueue the record. A failed confirmation remains an unknown outcome.
    """
    identifier(input_id)
    path = directory / (input_id + ".json")
    try:
        for folder in (*reversed(tuple(directory.parents)[:3]), directory):
            owned_directory(folder)
        fd = open_private(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    except ValueError as exc:
        raise ProtocolError("Invalid inbox storage boundary") from exc
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ProtocolError("Invalid inbox storage path") from exc
        raise
    with os.fdopen(fd, "rb") as stream:
        if os.fstat(stream.fileno()).st_nlink != 1:
            raise ProtocolError("Invalid inbox record link count")
        raw = stream.read(MAX_INPUT_RECORD + 1)
        if len(raw) > MAX_INPUT_RECORD:
            raise ProtocolError("Inbox record exceeds size limit")
        row = strict_json(raw)
        validate_input(row, input_id)
        if confirm:
            os.fsync(stream.fileno())
    if confirm:
        sync_directory(directory)
    return row


def validate_input(row, input_id):
    try:
        required = {"id", "status", "message", "origin", "accepted_at"}
        if (not isinstance(row, dict) or not required <= set(row)
                or set(row) - required - {"turn_options", "task_id", "service_address"}
                or row["id"] != input_id or row["status"] not in INPUT_STATES
                or row["origin"] not in {"chat", "quick", "startup"}
                or type(row["accepted_at"]) not in (int, float)
                or not math.isfinite(row["accepted_at"])):
            raise ValueError("Invalid input record")
        message = row["message"]
        if (not isinstance(message, dict) or message.get("kind") != "submit"
                or message.get("id") != input_id or not isinstance(message.get("text"), str)):
            raise ValueError("Invalid input identity")
        BoundContext.from_record(message["context"])
        if row.get("task_id") is not None:
            identifier(row["task_id"])
        address = row.get("service_address")
        if address is not None:
            if not isinstance(address, dict) or set(address) != {
                    "project_id", "service_id", "session_id", "runtime_id"}:
                raise ValueError("Invalid input address")
            for value in address.values():
                identifier(value)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ProtocolError("Invalid durable input record") from exc
