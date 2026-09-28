"""Private fsync-backed request state and receipts, never an executable replay log."""

from __future__ import annotations

import fcntl
from sicolock import lock as state_lock
import hashlib
import json
import os
import threading
import time
from pathlib import Path

from ..core.contracts import NeedsReconcile, json_copy
from ..storage.journal import open_private, private_dir, sync_directory
from .framing import strict_json

PROTOCOL = "cad_ai_router_journal.v1"
MAX_RECORD = 2 * 1024 * 1024


def record_digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


class RouterJournal:
    def __init__(self, directory, *, instance_id, generation, bridge_id, router_id, readonly=False):
        self.directory = Path(directory)
        self.identity = dict(instance_id=instance_id, generation=generation,
                             bridge_id=bridge_id, router_id=router_id)
        self.readonly = readonly
        self._lock = threading.RLock()
        self._rows = {}
        self._sequence = 0
        self._fd = -1
        self._writer = -1
        self._maintenance = -1
        self.writer_active = False
        if not readonly:
            private_dir(self.directory)
        try:
            try:
                self._maintenance = open_private(self.directory / "maintenance.lock",
                    os.O_RDONLY if readonly else os.O_CREAT | os.O_RDWR)
            except FileNotFoundError:
                if not readonly:
                    raise
            if self._maintenance >= 0:
                state_lock(self._maintenance, fcntl.LOCK_SH | fcntl.LOCK_NB)
            if not readonly:
                if (self.directory / "archive/manifest.json").exists():
                    raise ValueError("Archived router journal cannot become a writer")
                self._writer = open_private(self.directory / "writer.lock", os.O_CREAT | os.O_RDWR)
                state_lock(self._writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                self._writer = open_private(self.directory / "writer.lock", os.O_RDONLY)
                try:
                    state_lock(self._writer, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    self.writer_active = True
            if readonly and (self.directory / "archive/manifest.json").exists():
                from .router_archive import archived_journal

                with archived_journal(self.directory, self.identity) as (stream, metadata):
                    self._rows, self._sequence, _ = load_records(stream, self.identity, metadata)
            else:
                self._fd = open_private(self.directory / "requests.jsonl",
                                        os.O_RDONLY if readonly else os.O_CREAT | os.O_RDWR)
                self._load()
            if not readonly:
                sync_directory(self.directory)
                for row in tuple(self._rows.values()):
                    recovered = self._recovered(row)
                    if recovered != row:
                        self.record(recovered)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def request_id(value):
        if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("Invalid router request ID")
        return value

    @staticmethod
    def _recovered(row):
        state = row.get("state")
        if state in {"accepted", "queued", "cancel_requested", "running"}:
            return dict(row, state="timed_out_unknown" if state == "running" else "cancelled_before_start",
                        recovered=True, recovery_reason="bridge_stopped", recovered_at=time.time())
        return row

    def _load(self):
        with os.fdopen(os.dup(self._fd), "rb") as stream:
            self._rows, self._sequence, offset = load_records(stream, self.identity)
        if not self.readonly and os.fstat(self._fd).st_size != offset:
            os.ftruncate(self._fd, offset)
            os.fsync(self._fd)

    def record(self, record):
        with self._lock:
            if self.readonly or self._fd < 0:
                raise OSError("Router journal is read-only or closed")
            request_id = self.request_id(record["request_id"])
            row = dict(self._rows.get(request_id, {}), **record)
            # A caller timing out cannot overwrite a reply already durably received.
            previous = self._rows.get(request_id, {})
            if previous.get("state") == "late_reply" and record.get("state") in {
                "timed_out_unknown", "failed", "router_unavailable"
            }:
                row.update(state="late_reply", caller_state=record["state"])
            row.update(self.identity, automatic_resume_allowed=False)
            event = dict(protocol=PROTOCOL, identity=self.identity,
                         sequence=self._sequence + 1, record=row, sha256=record_digest(row))
            data = (json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode()
            if len(data) > MAX_RECORD:
                raise ValueError("Router receipt exceeds limit")
            try:
                os.lseek(self._fd, 0, os.SEEK_END)
                remaining = memoryview(data)
                while remaining:
                    count = os.write(self._fd, remaining)
                    if count <= 0:
                        raise OSError("Router journal write did not progress")
                    remaining = remaining[count:]
                os.fsync(self._fd)
            except BaseException:
                self.close()
                raise
            self._rows[request_id] = row
            self._sequence += 1

    def receipt(self, request_id, *, session_id, target_id, recovered=False):
        with self._lock:
            row = self._rows.get(self.request_id(request_id))
            if row is None:
                raise NeedsReconcile("Router receipt not found; no request was replayed")
            if row.get("session_id") != session_id or row.get("target_id") != target_id:
                raise NeedsReconcile("Router receipt owner or target mismatch")
            return json_copy(self._recovered(row) if recovered and not self.writer_active else row)

    def lookup(self, request_id):
        with self._lock:
            row = self._rows.get(request_id)
            return json_copy(row) if row else None

    def close(self):
        for name in ("_fd", "_writer", "_maintenance"):
            fd = getattr(self, name, -1)
            if fd >= 0:
                os.close(fd)
                setattr(self, name, -1)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def load_records(stream, identity, metadata=None):
    """Validate the original event sequence, including a preserved incomplete tail."""
    rows, sequence, offset, size = {}, 0, 0, 0
    digest = hashlib.sha256()
    while True:
        raw = stream.readline(MAX_RECORD + 1)
        if not raw:
            break
        size += len(raw)
        digest.update(raw)
        if len(raw) > MAX_RECORD or (metadata and size > metadata['size']):
            raise ValueError("Router journal record exceeds limit")
        if not raw.endswith(b"\n"):
            break
        event = strict_json(raw)
        if (event.get("protocol") != PROTOCOL or event.get("identity") != identity
                or event.get("sequence") != sequence + 1):
            raise ValueError("Router journal identity or sequence mismatch")
        row = event['record']
        if event.get('sha256') != record_digest(row):
            raise ValueError("Router journal checksum mismatch")
        rows[RouterJournal.request_id(row['request_id'])] = row
        sequence += 1
        offset += len(raw)
    if metadata and (size != metadata['size'] or digest.hexdigest() != metadata['sha256']):
        raise ValueError("Archived router journal checksum mismatch")
    return rows, sequence, offset
