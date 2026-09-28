"""Bounded read-only journal streams and tail windows."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from ..core.contracts import TASK_CONTRACT, BoundContext, identifier
from ..transport.framing import strict_json
from .index import (
    CHAT_KINDS,
    CONTEXT_KINDS,
    FLAG_CONTEXT,
    FLAG_DELETED,
    FLAG_ORIGIN,
    JournalIndex,
    read_records,
)
from .journal import MAX_RECORD, SessionJournal, open_private
from .native_origin import ORIGIN_EVENTS, NativeOrigins
from .history_policy import verify as verify_history


@dataclass(frozen=True)
class TailWindow:
    """Newest chat window of a closed journal, located without a full scan."""

    start: int
    offset: int
    committed: int
    context: dict
    deleted: bool
    scanned: int

def owned_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Expected a private session directory")

def validate_event(row):
    kind, payload = row["kind"], row["payload"]
    if kind in {"task.started", "session.created"} and "context" in payload:
        BoundContext.from_record(payload["context"])
    if kind in {"task.started", "model.delta", "model.completed"}:
        if not isinstance(payload.get("text"), str):
            raise ValueError("Invalid event text")
    if kind == "task.started" and "context" not in payload:
        raise ValueError("Missing task source")
    if kind.startswith("tool."):
        identifier(payload.get("id"))
        identifier(payload.get("name"))
    if kind == "tool.finished":
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("status"), str):
            raise ValueError("Invalid tool result")
        if "artifact" in result and not isinstance(result["artifact"], dict):
            raise ValueError("Invalid artifact reference")
    if kind == "context.compacted" and not isinstance(payload.get("artifact"), dict):
        raise ValueError("Invalid context archive")

class SessionReader:
    def __init__(self, root, session_id):
        self.root = Path(root)
        self.session_id = identifier(session_id)
        self.directory = self.root / "sessions" / session_id
        from .record_removal import assert_present

        assert_present(self.root, session_id)
        for path in (self.root, self.root / "sessions", self.directory):
            owned_directory(path)
        self.migration = verify_history(self.root, session_id)

    def events(self):
        fd = open_private(self.directory / "events.jsonl", os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            return list(self.iter_events(stream))

    def stream_events(self, sequence=0):
        """Iterate the journal without holding every record in memory."""
        fd = open_private(self.directory / "events.jsonl", os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            yield from self.iter_events(stream, sequence=sequence)

    def iter_raw_records(self, stream, *, sequence=0, validate=True):
        """Envelope-checked ``(row, offset, length)`` records."""

        parse = strict_json if validate else json.loads
        while True:
            offset = stream.tell()
            raw = stream.readline(MAX_RECORD + 1)
            if len(raw) > MAX_RECORD:
                raise ValueError("会话记录超过单条上限")
            if not raw or not raw.endswith(b"\n"):
                stream.seek(offset)
                return
            row = parse(raw)
            sequence += 1
            if (
                row.get("contract") != TASK_CONTRACT
                or row.get("session_id") != self.session_id
                or row.get("sequence") != sequence
                or not isinstance(row.get("kind"), str)
                or not isinstance(row.get("payload"), dict)
            ):
                raise ValueError("会话日志身份或顺序不匹配")
            if validate:
                validate_event(row)
            yield row, offset, len(raw)

    def iter_raw(self, stream, *, sequence=0, validate=True):
        """Envelope-checked rows.

        ``validate=False`` is the metadata fast path: no payload deep checks and
        plain JSON parsing (no duplicate-key/non-finite guards), which measurably
        speeds up catalog scans on long journals. Authoritative readers always
        parse strictly.
        """
        for row, _offset, _length in self.iter_raw_records(
                stream, sequence=sequence, validate=validate):
            yield row

    def iter_events(self, stream, *, sequence=0, validate=True):
        """Validate an append-only stream, leaving an incomplete tail unread.

        Journals are never truncated and reads are never capped: only the
        newest messages are rendered, so a long history stays browsable.
        """
        for row in self.iter_raw(stream, sequence=sequence, validate=validate):
            yield SessionJournal.public(row)

    @staticmethod
    def _reverse_lines(stream, block=1 << 20):
        """Yield ``(line, offset)`` from the last complete line backwards."""

        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        end, carry = size, b""
        while end > 0:
            start = max(0, end - block)
            stream.seek(start)
            data = stream.read(end - start) + carry
            lines = data.split(b"\n")
            starts, position = [], start
            for line in lines:
                starts.append(position)
                position += len(line) + 1
            # ``lines[-1]`` is either empty (data ends on a newline) or the
            # unfinished file tail; both stay unread, like a forward reader.
            # ``lines[0]`` is carried left until a block starts at the file
            # beginning, where it becomes a complete first line.
            carry = lines[0] + (b"\n" if len(lines) > 1 else b"")
            first = 0 if start == 0 else 1
            for index in range(len(lines) - 2, first - 1, -1):
                yield lines[index], starts[index]
            end = start

    def journal_fingerprint(self, stream):
        info = os.fstat(stream.fileno())
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def journal_index(self):
        """Load or rebuild the sidecar index; ``None`` keeps the scan fallback."""

        fd = open_private(self.directory / "events.jsonl", os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            fingerprint = self.journal_fingerprint(stream)
            try:
                index, exact = JournalIndex.load(self.directory, fingerprint)
            except (OSError, ValueError, TypeError):
                index, exact = None, False
            if index is not None and exact:
                return index
            if index is None:
                index = JournalIndex(fingerprint)
            try:
                index.scan(self, stream)
            except (OSError, ValueError, KeyError, TypeError):
                return None
            index.fingerprint = fingerprint
            index.save(self.directory)
            return index

    def _index_window(self, index, start, offset):
        context = {}
        for entry in reversed(index.select(FLAG_CONTEXT)):
            payload = read_records(self.directory / "events.jsonl", [entry])[0].get("payload")
            value = payload.get("context") if isinstance(payload, dict) else None
            if isinstance(value, dict):
                context = value
                break
        return TailWindow(start=start, offset=offset, committed=index.committed,
                          context=context, deleted=index.has(FLAG_DELETED), scanned=0)

    def _indexed_window(self, index, messages):
        start, offset = index.find_start(messages)
        return self._index_window(index, start, offset)

    def tail_window(self, messages):
        """Locate the newest ``messages`` chat boundaries from the file end.

        Mirrors ``SessionJournal.replay_start`` for closed journals: the window
        starts at the task that owns the oldest message in it. The returned
        offset lets a caller stream the window without reading the prefix.
        """

        if type(messages) is not int or messages <= 0:
            raise ValueError("Invalid replay window")
        index = self.journal_index()
        if index is not None:
            return self._indexed_window(index, messages)
        return self._scanned_window(messages)

    def tail_task_window(self):
        """Window for the newest task: the first screen of a long session."""

        index = self.journal_index()
        if index is not None:
            start, offset = index.find_task_start()
            return self._index_window(index, start, offset)
        return self._scanned_task_window()

    def _scanned_task_window(self):
        """Fallback locator for the newest task: read backwards until it starts."""

        scanned = 0
        start, offset, committed, deleted = 1, 0, 0, False
        context: dict = {}
        fd = open_private(self.directory / "events.jsonl", os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            for line, line_offset in self._reverse_lines(stream):
                scanned += 1
                row = json.loads(line)
                sequence = row.get("sequence")
                if (
                    row.get("contract") != TASK_CONTRACT
                    or row.get("session_id") != self.session_id
                    or not isinstance(row.get("kind"), str)
                    or type(sequence) is not int
                    or sequence <= 0
                    or not isinstance(row.get("payload"), dict)
                ):
                    raise ValueError("会话日志身份或顺序不匹配")
                if committed == 0:
                    committed = sequence
                kind = row["kind"]
                if kind == "codex.thread.deleted":
                    deleted = True
                if not context and kind in CONTEXT_KINDS:
                    value = row["payload"].get("context")
                    if isinstance(value, dict):
                        context = value
                if kind == "task.started":
                    start, offset = sequence, line_offset
                    break
        return TailWindow(start=start, offset=offset, committed=committed,
                          context=context, deleted=deleted, scanned=scanned)

    def _scanned_window(self, messages):
        """Fallback locator: read the journal backwards from its end."""

        boundaries, scanned = 0, 0
        start, offset, committed, deleted = 1, 0, 0, False
        context: dict = {}
        fd = open_private(self.directory / "events.jsonl", os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            for line, line_offset in self._reverse_lines(stream):
                scanned += 1
                # The locator only finds offsets and sequences; the window is
                # re-read strictly when it is streamed, so plain JSON is
                # enough here and measurably cheaper on a long journal.
                row = json.loads(line)
                sequence = row.get("sequence")
                if (
                    row.get("contract") != TASK_CONTRACT
                    or row.get("session_id") != self.session_id
                    or not isinstance(row.get("kind"), str)
                    or type(sequence) is not int
                    or sequence <= 0
                ):
                    raise ValueError("会话日志身份或顺序不匹配")
                if committed == 0:
                    committed = sequence
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    raise ValueError("会话日志身份或顺序不匹配")
                kind = row["kind"]
                if not context and kind in {"session.created", "task.started"}:
                    value = payload.get("context")
                    if isinstance(value, dict):
                        context = value
                if kind == "codex.thread.deleted":
                    deleted = True
                if kind in CHAT_KINDS:
                    boundaries += 1
                    if boundaries >= messages and kind == "task.started":
                        start, offset = sequence, line_offset
                        break
        return TailWindow(start=start, offset=offset, committed=committed,
                          context=context, deleted=deleted, scanned=scanned)

    def origins_before(self, offset):
        """Native-origin index for the prefix ending at ``offset``.

        Only origin-bearing kinds are observed, so seeding a tail window does
        not repeat the payload validation of the full read path.
        """

        origins = NativeOrigins()
        if not offset:
            return origins
        index = self.journal_index()
        if index is not None:
            entries = index.select(FLAG_ORIGIN, before=offset)
            for row in read_records(self.directory / "events.jsonl", entries):
                try:
                    origins.observe(row)
                except (KeyError, TypeError) as exc:
                    raise ValueError("会话日志身份或顺序不匹配") from exc
            return origins
        fd = open_private(self.directory / "events.jsonl", os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            while stream.tell() < offset:
                raw = stream.readline(MAX_RECORD + 1)
                if len(raw) > MAX_RECORD:
                    raise ValueError("会话记录超过单条上限")
                if not raw or not raw.endswith(b"\n"):
                    break
                row = strict_json(raw)
                if row.get("kind") not in ORIGIN_EVENTS:
                    continue
                try:
                    origins.observe(row)
                except (KeyError, TypeError) as exc:
                    raise ValueError("会话日志身份或顺序不匹配") from exc
        return origins

    def artifact_text(self, path, sha256, *, max_bytes=MAX_RECORD):
        for folder in (self.root / "attachments", self.root / "attachments" / self.session_id):
            owned_directory(folder)
        return SessionJournal.artifact_text(self, path, sha256, max_bytes=max_bytes)
