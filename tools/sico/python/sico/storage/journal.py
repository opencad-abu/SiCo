"""Durable event+state transactions; an incomplete final line is the only recoverable tail."""

from __future__ import annotations

import fcntl
from sicolock import lock as state_lock
import hashlib
import json
import os
import re
import stat
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..core.contracts import TASK_CONTRACT, RunState, identifier, json_copy
from ..transport.framing import strict_json
from .names import NAME_EVENTS, session_names
from .native_origin import ORIGIN_EVENTS, NativeOrigins
from .history_policy import assert_writable
from .roots import agent_root, state_root

MAX_RECORD = 16 * 1024 * 1024


def private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError(f"Expected a private owned directory: {path}")


def open_private(path: Path, flags: int) -> int:
    fd = os.open(path, (flags & ~os.O_TRUNC) | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise ValueError("Expected a private owned regular file")
    if flags & os.O_TRUNC:
        os.ftruncate(fd, 0)
    return fd


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class SessionJournal:
    def __init__(self, launch_dir: str | Path, session_id: str):
        self._events_lock = threading.RLock()
        identifier(session_id)
        # absolute() preserves a shared NAS alias, unlike resolve().
        launch = Path(launch_dir).absolute()
        if not launch.is_dir():
            raise ValueError("Launch directory is unavailable")
        root = state_root(launch, create=True)
        assert_writable(agent_root(launch), session_id)
        from .record_removal import assert_present

        assert_present(agent_root(launch), session_id)
        ai = root / "ai"
        ai.mkdir(mode=0o700, exist_ok=True)
        if not stat.S_ISDIR(ai.lstat().st_mode) or ai.stat().st_uid != os.getuid():
            raise ValueError("Invalid SiCo state directory")
        # Isolate the new agent from existing bridge/terminal runtime files.
        self.root = ai / "agent"
        private_dir(self.root)
        for name in ("sessions", "attachments"):
            private_dir(self.root / name)
        self.directory = self.root / "sessions" / session_id
        private_dir(self.directory)
        self.session_id = session_id
        self.attachments = self.root / "attachments" / session_id
        private_dir(self.attachments)
        self._lock = open_private(self.directory / "writer.lock", os.O_CREAT | os.O_RDWR)
        try:
            state_lock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._lock)
            self._lock = -1
            raise RuntimeError("Session already has an active writer") from None
        try:
            assert_present(self.root, session_id)
            self._fd = open_private(self.directory / "events.jsonl", os.O_CREAT | os.O_RDWR)
            self.records = self._read()
            # 删除标记是终态元数据：加载完记录即可判定，不再单独做一次全量历史扫描。
            self.deleted = any(row["kind"] == "codex.thread.deleted" for row in self.records)
            self._name_events = [row for row in self.records if row["kind"] in NAME_EVENTS]
            self._name_metadata = session_names(self._name_events)
            self._native_events = [row for row in self.records if row["kind"] in ORIGIN_EVENTS]
            for directory in (
                self.directory,
                self.directory.parent,
                self.attachments.parent,
                self.root,
            ):
                sync_directory(directory)
        except BaseException:
            self.close()
            raise

    def _read(self) -> list[dict]:
        rows = []
        offset = 0
        with os.fdopen(os.dup(self._fd), "rb") as stream:
            stream.seek(0)
            while True:
                raw = stream.readline(MAX_RECORD + 1)
                if not raw:
                    break
                if len(raw) > MAX_RECORD:
                    raise ValueError("Journal record too large")
                if not raw.endswith(b"\n"):
                    os.ftruncate(self._fd, offset)
                    os.fsync(self._fd)
                    break
                row = strict_json(raw)
                if (
                    row.get("contract") != TASK_CONTRACT
                    or row.get("sequence") != len(rows) + 1
                    or row.get("session_id") != self.session_id
                ):
                    raise ValueError("Corrupt journal sequence or contract")
                RunState.from_record(row["state"])
                if not isinstance(row.get("kind"), str) or not isinstance(row.get("payload"), dict):
                    raise ValueError("Corrupt journal event")
                rows.append(row)
                offset += len(raw)
        return rows

    @property
    def state(self) -> RunState | None:
        return RunState.from_record(self.records[-1]["state"]) if self.records else None

    # Chat boundaries: one user bubble per turn and one answer bubble per model call.
    CHAT_KINDS = ("task.started", "model.completed")

    def replay_start(self, messages):
        """First sequence of the newest ``messages`` chat turns, task aligned.

        A viewer that only shows the tail does not need the whole journal, and
        the oldest records are the most expensive to copy.
        """

        records = self.records
        index, boundaries = len(records), 0
        while index > 0 and boundaries < messages:
            index -= 1
            if records[index]["kind"] in self.CHAT_KINDS:
                boundaries += 1
        # Walk back to the task that owns the oldest message in the window, so
        # the viewer still sees a task.started before its deltas and tools.
        while index > 0 and records[index]["kind"] != "task.started":
            index -= 1
        return records[index]["sequence"] if records else 1

    def prepare_replay(self, messages, *, after=None):
        """Capture one committed tail boundary and its prefix origins in a worker.

        The append lock includes fsync and publication to ``records``. Holding
        it across all three reads prevents a moving tail from mixing boundaries
        or admitting origins from an event that has not committed yet.
        """
        with self._events_lock:
            if after is None:
                start = self.replay_start(messages)
            else:
                if type(after) is not int or not 0 <= after <= len(self.records):
                    raise ValueError("Invalid replay restart sequence")
                if after and (after == len(self.records)
                              or self.records[after]["kind"] != "task.started"):
                    raise ValueError("Replay restart must use the captured task boundary")
                start = after + 1
            return start, len(self.records), self.native_origins(start - 1)

    def append(self, kind: str, payload: dict, state: RunState) -> dict:
        with self._events_lock:
            return self._append(kind, payload, state)

    def append_binding(self, kind, payload, initial_context):
        """Session events preserve the latest durable task state while a loop runs."""
        if not kind.startswith("binding.") or payload.get("session_id") != self.session_id:
            raise ValueError("Invalid session binding event")
        with self._events_lock:
            state = self.state or RunState(initial_context)
            return self._append(kind, payload, state)

    def append_session(self, kind, payload, initial_context):
        """Lifecycle facts preserve the latest committed task state."""
        if not kind.startswith("session."):
            raise ValueError("Invalid session lifecycle event")
        with self._events_lock:
            return self._append(kind, payload, self.state or RunState(initial_context))

    def _append(self, kind: str, payload: dict, state: RunState) -> dict:
        if getattr(self, "_fd", -1) < 0:
            raise RuntimeError("Journal is closed")
        row = {
            "contract": TASK_CONTRACT,
            "sequence": len(self.records) + 1,
            "session_id": self.session_id,
            "task_id": state.task.get("id", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "payload": json_copy(payload),
            "state": state.record(),
        }
        data = (json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(data) > MAX_RECORD:
            raise ValueError("Journal record exceeds limit")
        os.lseek(self._fd, 0, os.SEEK_END)
        view = memoryview(data)
        try:
            while view:
                written = os.write(self._fd, view)
                if written <= 0:
                    raise OSError("Journal write did not progress")
                view = view[written:]
            os.fsync(self._fd)
        except BaseException:
            self.close()  # A partial write must be recovered by reopening, never appended to.
            raise
        self.records.append(row)
        if kind in ORIGIN_EVENTS:
            self._native_events.append(row)
        if kind in NAME_EVENTS:
            self._name_events.append(row)
            self._name_metadata = session_names(self._name_events)
        return self.public(row)

    @property
    def name_metadata(self):
        """Detached snapshot published after durable append, without waiting for I/O."""
        return dict(self._name_metadata)

    def native_origins(self, before):
        """Small origin index for a chat tail; never copy historical output blobs."""
        origins = NativeOrigins()
        with self._events_lock:
            for row in self._native_events:
                if row["sequence"] > before:
                    break
                origins.observe(row)
        return origins

    @staticmethod
    def public(row: dict) -> dict:
        return json_copy({key: value for key, value in row.items() if key != "state"})

    def events(self, after: int = 0, *, limit: int | None = None,
               blocking: bool = True) -> list[dict] | None:
        if type(after) is not int or after < 0:
            raise ValueError("Invalid event cursor")
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError("Invalid event limit")
        if not self._events_lock.acquire(blocking=blocking):
            return None
        try:
            end = None if limit is None else after + limit
            return [self.public(row) for row in self.records[after:end]]
        finally:
            self._events_lock.release()

    def committed_batch(self, after, limit, max_bytes):
        """Worker-only bounded read; one oversized raw record stays in the worker."""
        with self._events_lock:
            committed, events, size = len(self.records), [], 0
            for row in self.records[after:after + limit]:
                event = self.public(row)
                cost = len(json.dumps(event, ensure_ascii=False).encode("utf-8"))
                if events and size + cost > max_bytes:
                    break
                events.append(event)
                size += cost
                if size >= max_bytes:
                    break
            return events, committed

    def artifact(self, data: bytes) -> dict:
        return self._write_artifact(data, ".json")

    def text_attachment(self, text: str) -> dict:
        """Persist user supplied clipboard text as a checksummed UTF-8 attachment."""
        if not isinstance(text, str) or "\0" in text:
            raise ValueError("Text attachment must be valid text")
        return self._write_artifact(text.encode("utf-8"), ".txt")

    def _write_artifact(self, data: bytes, suffix: str) -> dict:
        if len(data) > MAX_RECORD:
            raise ValueError("Artifact exceeds size limit")
        if suffix not in {".json", ".txt"}:
            raise ValueError("Unsupported artifact type")
        digest = hashlib.sha256(data).hexdigest()
        target = self.attachments / (uuid.uuid4().hex + suffix)
        temporary = target.with_suffix(".part")
        fd = open_private(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        sync_directory(self.attachments)
        return {
            "path": str(target.relative_to(self.root)),
            "sha256": digest,
            "size": len(data),
            "complete": True,
        }

    def read_artifact(self, path: str, sha256: str, offset: int = 0, limit: int = 4000) -> dict:
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 4000
        ):
            raise ValueError("Artifact offset must be >= 0 and limit between 1 and 4000 characters")
        content = self.artifact_text(path, sha256)
        return {
            "text": content[offset : offset + limit],
            "offset": offset,
            "next_offset": min(len(content), offset + limit),
            "total_chars": len(content),
        }

    def artifact_text(self, path: str, sha256: str, *, max_bytes: int = MAX_RECORD) -> str:
        relative = Path(path)
        if (
            relative.parent != Path("attachments") / self.session_id
            or not re.fullmatch(r"[a-f0-9]{32}\.(?:json|txt)", relative.name)
            or not re.fullmatch(r"[a-f0-9]{64}", sha256)
            or type(max_bytes) is not int
            or not 1 <= max_bytes <= MAX_RECORD
        ):
            raise ValueError(
                "Read only attachments/" + self.session_id
                + "/<32 lowercase hex>.json or .txt with its 64-hex sha256; "
                "measurement results use query_maestro_results"
            )
        fd = open_private(self.root / relative, os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes or hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError("Artifact checksum or size mismatch")
        return data.decode("utf-8")

    def close(self) -> None:
        for key in ("_fd", "_lock"):
            fd = getattr(self, key, -1)
            if fd >= 0:
                os.close(fd)
                setattr(self, key, -1)

    def __enter__(self) -> SessionJournal:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
