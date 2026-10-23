"""Validate version-pinned process notifications without interpreting shell text."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessEvent:
    kind: str
    sequence: int
    stream: str | None = None
    data: bytes = b""
    exit_code: int | None = None
    sandbox_denied: bool = False


def notification(value, process_id, expected_sequence=None):
    """One event sequence includes output, actual exit and stream closure.

    A gap must resolve before publishing subsequent events or complete output.
    Exit alone does not prove output drain or descendant cleanup.
    """
    if not isinstance(value, dict) or set(value) != {"method", "params"}:
        raise ValueError("Invalid exec-server notification")
    kind = value["method"]
    fields = {"process/output": {"stream", "chunk"},
              "process/exited": {"exitCode", "sandboxDenied"}, "process/closed": set()}
    data = value["params"]
    if (kind not in fields or not isinstance(data, dict)
            or set(data) != {"processId", "seq"} | fields[kind]
            or data["processId"] != process_id
            or type(data["seq"]) is not int or not 1 <= data["seq"] <= 2**63 - 1
            or expected_sequence is not None and data["seq"] != expected_sequence):
        raise ValueError("Exec-server process identity, event kind or sequence mismatch")
    if kind == "process/output":
        if data["stream"] not in ("stdout", "stderr") or not isinstance(data["chunk"], str):
            raise ValueError("Invalid exec-server output stream")
        if len(data["chunk"]) > 2 * 65536:
            raise ValueError("Exec-server output chunk exceeds limit")
        try:
            raw = base64.b64decode(data["chunk"], validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Invalid exec-server output encoding") from None
        if not raw or len(raw) > 65536:
            raise ValueError("Invalid exec-server output size")
        return ProcessEvent("output", data["seq"], data["stream"], raw)
    if kind == "process/exited":
        if (type(data["exitCode"]) is not int or not 0 <= data["exitCode"] <= 255
                or type(data["sandboxDenied"]) is not bool):
            raise ValueError("Invalid exec-server process exit")
        return ProcessEvent("exited", data["seq"], exit_code=data["exitCode"],
                            sandbox_denied=data["sandboxDenied"])
    return ProcessEvent("closed", data["seq"])


class ProcessEvents:
    """Reorder concurrent stdout/stderr notifications within a fixed budget.

    The pinned binary can deliver streams out of sequence. A later arrival may
    fill a gap; no byte or closure is published beyond that gap.
    """

    def __init__(self, process_id):
        self._process_id = process_id
        self._next = 1
        self._pending = {}

    def push(self, value):
        event = notification(value, self._process_id)
        if event.sequence < self._next or event.sequence in self._pending:
            raise ValueError("Duplicate exec-server event")
        if (len(self._pending) >= 128 or event.sequence - self._next >= 128
                or len(event.data) + sum(len(row.data) for row in self._pending.values()) > 1024 * 1024):
            raise ValueError("Exec-server event gap exceeded reordering budget")
        self._pending[event.sequence] = event

    @property
    def waiting(self):
        return bool(self._pending) and self._next not in self._pending

    def pop(self):
        event = self._pending.pop(self._next, None)
        if event is not None:
            if event.kind == "closed" and self._pending:
                raise ValueError("Exec-server events continued beyond stream closure")
            self._next += 1
        return event
