"""Bounded append-only state events for project-level visible-DRC jobs."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "cad.visible_drc.event.v1"
_JOB = re.compile(r"^visible-[a-f0-9]{32}$")
_STATES = frozenset({
    "queued",
    "running",
    "clean",
    "violations",
    "failed",
    "unknown",
    "correct",
    "incorrect",
    "cancelled",
})
_MAX_EVENTS = 64
_MAX_BYTES = 64 * 1024


def _text(value, field, *, maximum=512):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} is invalid")
    if len(value) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{field} is invalid")
    return value


def _message(value):
    return " ".join(str(value or "").split())[:512]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class VisibleDrcEvents:
    def __init__(self, directory):
        self.directory = Path(directory)

    def _path(self, job_ref):
        if not isinstance(job_ref, str) or _JOB.fullmatch(job_ref) is None:
            raise ValueError("job_ref is invalid")
        return self.directory / (job_ref + ".jsonl")

    def _ensure_directory(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid()
        ):
            raise OSError("Visible DRC event directory is unavailable")
        os.chmod(self.directory, 0o700)

    def _lock(self, job_ref):
        path = self.directory / (job_ref + ".lock")
        descriptor = os.open(
            str(path),
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor

    def _read(self, job_ref):
        path = self._path(job_ref)
        try:
            descriptor = os.open(
                str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
        except FileNotFoundError:
            return []
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_size > _MAX_BYTES
            ):
                raise OSError("Visible DRC event file is invalid")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                payload = stream.read(_MAX_BYTES + 1)
        finally:
            if descriptor is not None:
                os.close(descriptor)
        events = []
        for line in payload.splitlines():
            try:
                row = json.loads(line.decode("utf-8"))
                if not isinstance(row, dict):
                    continue
                if (
                    row.get("schema") != SCHEMA
                    or type(row.get("seq")) is not int
                    or row["seq"] <= 0
                    or row.get("state") not in _STATES
                ):
                    continue
                event = {
                    "schema": SCHEMA,
                    "seq": row["seq"],
                    "state": row["state"],
                    "at": _text(row.get("at"), "at", maximum=64),
                    "message": _message(row.get("message")),
                }
                if events and event["seq"] <= events[-1]["seq"]:
                    continue
                events.append(event)
            except (UnicodeDecodeError, ValueError, TypeError):
                continue
        return events

    def read(self, job_ref, after=0, limit=32):
        if type(after) is not int or after < 0:
            raise ValueError("after is invalid")
        if type(limit) is not int or not 1 <= limit <= _MAX_EVENTS:
            raise ValueError("limit is invalid")
        events = [event for event in self._read(job_ref) if event["seq"] > after]
        return events[-limit:]

    def append(self, job_ref, state, message=""):
        if state not in _STATES:
            raise ValueError("state is invalid")
        self._ensure_directory()
        descriptor = self._lock(job_ref)
        try:
            events = self._read(job_ref)
            if events and events[-1]["state"] == state:
                return events[-1]
            seq = events[-1]["seq"] + 1 if events else 1
            event = {
                "schema": SCHEMA,
                "seq": seq,
                "state": state,
                "at": _now(),
                "message": _message(message),
            }
            line = (
                json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            if len(events) >= _MAX_EVENTS:
                events = events[-(_MAX_EVENTS // 2):] + [event]
                payload = b"".join(
                    (json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n")
                    .encode("utf-8")
                    for row in events
                )
                path = self._path(job_ref)
                temporary = path.with_name(
                    "." + path.name + "." + str(os.getpid()) + "." + uuid.uuid4().hex
                )
                with open(temporary, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            else:
                path = self._path(job_ref)
                target = os.open(
                    str(path),
                    os.O_CREAT | os.O_APPEND | os.O_WRONLY
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                try:
                    os.write(target, line)
                finally:
                    os.close(target)
            return event
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def remove(self, job_ref):
        for suffix in (".jsonl", ".lock"):
            try:
                (self.directory / (job_ref + suffix)).unlink()
            except FileNotFoundError:
                pass


__all__ = ["SCHEMA", "VisibleDrcEvents"]
