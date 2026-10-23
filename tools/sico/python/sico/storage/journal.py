"""Durable event+state transactions; an incomplete final line is the only recoverable tail."""

from __future__ import annotations

import fcntl
from sicolock import lock as state_lock
import hashlib
import json
import os
import stat
import threading
import uuid
from collections import OrderedDict
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from ..core.contracts import TASK_CONTRACT, RunState, identifier, json_copy
from .journal_records import MAX_RECORD, decode_record, fingerprint, validate_state
from .artifact_reference import validate as _artifact_reference
from .names import NAME_EVENTS, session_names
from ..core.native_origin import ORIGIN_EVENTS, NativeOrigins
from .history_policy import assert_writable
from .roots import agent_root, state_root

# 首屏窗口的行数上限：回合数不足 ``messages`` 的超长会话（现场一份 65 万行、
# 只有 79 个回合的日志）否则会把整篇历史当作“最近 N 个回合”重播给界面。
REPLAY_WINDOW_ROWS = 16384
# 初始化扫描只需事件信封与少量状态行；这些高频整篇拷贝的 kind 永不参与
# 恢复/绑定/codex 启动的推导，读取时显式排除。
BULK_KINDS = frozenset({"model.delta", "model.status", "model.completed",
                        "router.status", "token.usage"})
# 行类索引据此给行打旗标：状态行（最小可跳集合）与传输噪声（含 delta）。
STATUS_KINDS = frozenset({"model.status", "router.status"})
TRANSIENT_KINDS = frozenset({"model.delta"}) | STATUS_KINDS


def replay_window_rows():
    """Rows a first-screen window may walk back (module global: shrunken in tests)."""

    return REPLAY_WINDOW_ROWS


class LazyRecords(Sequence):
    """Durable records materialized on access, located through the sidecar index.

    现场一份 9.7GB 日志里 99.8% 的行是展示噪声（delta/status），初始化只用到
    索引位置与少数行类；打开时不再整篇解析，读取时按索引偏移单行严格解析。
    """

    def __init__(self, journal):
        self._journal = journal

    def __len__(self):
        return self._journal._index.committed

    def __getitem__(self, position):
        if isinstance(position, slice):
            return [self[index] for index in range(*position.indices(len(self)))]
        return self._journal.row(position)

    def append(self, row):
        raise RuntimeError("Append through the journal, not its record view")


def durable_task(task):
    """Durable rows keep the task without its display-only text copy.

    ``text`` is the accumulated display copy of the task output; it stays in the
    live state and the model.delta/model.completed stream owns the content, so
    comparing states written before and after the split must ignore it.
    """
    view = dict(task or {})
    view.pop("text", None)
    return view


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
            self._row_cache = OrderedDict()
            from .journal_recovery import writable_index

            self._index = writable_index(self._fd, self.directory, self.session_id)
            self.records = LazyRecords(self)
            from .index import FLAG_DELETED, FLAG_METADATA, FLAG_ORIGIN

            # 删除标记/名称/起源行类可直接从索引定位，无需解析整篇历史。
            self.deleted = self._index.has(FLAG_DELETED)
            self._name_events = [row for row in self._flagged_rows(FLAG_METADATA)
                                 if row["kind"] in NAME_EVENTS]
            self._name_metadata = session_names(self._name_events)
            self._native_events = self._flagged_rows(FLAG_ORIGIN)
            if self.records:
                self.row(len(self.records) - 1)     # 尾行严格校验（含状态）
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

    def _row_at(self, sequence, offset, length):
        raw = os.pread(self._fd, length, offset)
        if len(raw) != length or not raw.endswith(b"\n"):
            raise ValueError("会话记录索引与日志不一致")
        row = decode_record(raw, self.session_id, sequence)
        validate_state(row)
        return row

    def row(self, position):
        """One durable record by 0-based position; parsed strictly and cached."""

        count = len(self.records)
        if position < 0:
            position += count
        if not 0 <= position < count:
            raise IndexError(position)
        cached = self._row_cache.get(position)
        if cached is not None:
            return cached
        row = self._row_at(self._index.sequences[position], self._index.offsets[position],
                           self._index.lengths[position])
        self._row_cache[position] = row
        if len(self._row_cache) > 256:
            self._row_cache.popitem(last=False)
        return row

    def _flagged_rows(self, flag):
        rows = []
        for position in range(len(self._index.sequences)):
            if self._index.flags[position] & flag:
                rows.append(self.row(position))
        return rows

    def task_rows(self):
        """All ``task.started`` rows, index-located (a handful per session)."""

        from .index import FLAG_TASK

        return self._flagged_rows(FLAG_TASK)

    def latest_context(self):
        """Newest captured context among ``session.created``/``task.started`` rows."""

        from .index import FLAG_CONTEXT

        for position in range(len(self._index.sequences) - 1, -1, -1):
            if self._index.flags[position] & FLAG_CONTEXT:
                value = self.row(position)["payload"].get("context")
                if isinstance(value, dict):
                    return value
        return {}

    @property
    def state(self) -> RunState | None:
        return RunState.from_record(self.records[-1]["state"]) if self.records else None

    # Chat boundaries: one user bubble per turn and one answer bubble per model call.
    CHAT_KINDS = ("task.started", "model.completed")

    def replay_start(self, messages):
        """First sequence of the newest bounded chat window, index-located.

        窗口算法与只读阅读器共用 ``JournalIndex.find_start``：行数上限、
        任务对齐与中途开始都只查索引标志，不解析任何行。
        """

        if not len(self.records):
            return 1
        start, _offset = self._index.find_start(messages)
        return start

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
                if after and after == len(self.records):
                    raise ValueError("Replay restart must precede the journal head")
                # 重连按客户端捕获的窗口起点原样重放；起点可在任务中途，
                # 游标由 prepare_replay 按该行状态播种。
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
            "state": self.durable_state(state),
        }
        data = (json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(data) > MAX_RECORD:
            raise ValueError("Journal record exceeds limit")
        if fingerprint(self._fd) != self._index.fingerprint:
            raise ValueError("会话日志在写入期间发生外部变化")
        offset = os.lseek(self._fd, 0, os.SEEK_END)
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
        from .index import flags_for

        self._index.add(row["sequence"], offset, len(data), flags_for(kind))
        self._index.fingerprint = fingerprint(self._fd)
        self._row_cache[row["sequence"] - 1] = row
        if len(self._row_cache) > 256:
            self._row_cache.popitem(last=False)
        if kind in ORIGIN_EVENTS:
            self._native_events.append(row)
        if kind in NAME_EVENTS:
            self._name_events.append(row)
            self._name_metadata = session_names(self._name_events)
        return self.public(row)

    @staticmethod
    def durable_state(state):
        """行内状态不重复携带任务文本：完整内容按 model.delta/completed 事件留档。

        任务文本是任务输出的展示副本（事件流的逐字拷贝）；逐行携带会把日志按
        行数放大——现场一份 9.7GB 日志里它占了 8.3GB。
        """
        record = state.record()
        record["task"] = durable_task(record.get("task"))
        return record

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
               blocking: bool = True, exclude: frozenset | None = None) -> list[dict] | None:
        if type(after) is not int or after < 0:
            raise ValueError("Invalid event cursor")
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError("Invalid event limit")
        if not self._events_lock.acquire(blocking=blocking):
            return None
        try:
            end = None if limit is None else after + limit
            if exclude is None:
                return [self.public(row) for row in self.records[after:end]]
            # 排除展示行时先看索引行类：选择覆盖排除集的旗标预筛，现场 99.8%
            # 的行无需解析；旗标只是加速，精确过滤仍按 kind 执行。
            from .index import FLAG_STATUS, FLAG_TRANSIENT

            if TRANSIENT_KINDS <= exclude:
                flag = FLAG_TRANSIENT
            elif STATUS_KINDS <= exclude:
                flag = FLAG_STATUS
            else:
                flag = 0
            rows = []
            for position in range(after, len(self._index.sequences)):
                sequence = self._index.sequences[position]
                if end is not None and sequence > end:
                    break
                if flag and self._index.flags[position] & flag:
                    continue
                row = self.row(position)
                if row["kind"] not in exclude:
                    rows.append(self.public(row))
            return rows
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
        relative = _artifact_reference(path, sha256, self.session_id)
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_RECORD:
            raise ValueError("Invalid artifact read size")
        fd = open_private(self.root / relative, os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes or hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError("Artifact checksum or size mismatch")
        return data.decode("utf-8")

    def close(self) -> None:
        with self._events_lock:
            fd = getattr(self, "_fd", -1)
            if fd >= 0 and hasattr(self, "_index"):
                self._index.save_current(fd, self.directory)
            for key in ("_fd", "_lock"):
                fd = getattr(self, key, -1)
                if fd >= 0:
                    os.close(fd)
                    setattr(self, key, -1)

    def snapshot_events(self):
        """Read only the committed prefix published by this existing owner."""
        from .journal_snapshot import writer_snapshot

        return writer_snapshot(self)

    def __enter__(self) -> SessionJournal:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
