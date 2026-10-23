"""Sidecar offset index for one journal: sequence, offset, length, kind flags.

The index is a cache, never an authority: integrity or fingerprint mismatches
discard it, and callers fall back to scanning the journal. Authoritative
reads still parse records (strictly) themselves; the index only says where a
record starts and what class of content it carries, which is enough to jump
straight to the tail window, the native origins or the catalog metadata
without replaying the whole prefix.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cadai.json_values import strict_json
from .journal import STATUS_KINDS, TRANSIENT_KINDS, open_private, replay_window_rows
from .index_cache import INDEX_CONTRACT, INDEX_NAME, load_index, save_index
from .journal_records import fingerprint, iter_raw_records, validate_state
from ..core.native_origin import ORIGIN_EVENTS

# Content classes carried by one record, used to select without parsing.
FLAG_ORIGIN = 1        # native_origin.ORIGIN_EVENTS (origin state for a tail)
FLAG_METADATA = 2      # catalog metadata (thread names, children, deletion)
FLAG_CHAT = 4          # task.started / model.completed (viewer window)
FLAG_TASK = 8          # task.started (a window may only start here)
FLAG_TRANSIENT = 16    # terminal replay drops these after coalescing
FLAG_CONTEXT = 32      # carries the bound context for the session
FLAG_DELETED = 64      # codex.thread.deleted (terminal marker)
FLAG_STATUS = 128      # model.status / router.status（归档投影直接丢弃）

METADATA_KINDS = frozenset({
    "codex.thread", "codex.thread.name", "codex.thread.title",
    "codex.thread.deleted", "codex.child",
})
CHAT_KINDS = frozenset({"task.started", "model.completed"})
CONTEXT_KINDS = frozenset({"session.created", "task.started"})


def flags_for(kind):
    flags = 0
    if kind in ORIGIN_EVENTS:
        flags |= FLAG_ORIGIN
    if kind in METADATA_KINDS:
        flags |= FLAG_METADATA
    if kind in CHAT_KINDS:
        flags |= FLAG_CHAT
    if kind == "task.started":
        flags |= FLAG_TASK
    if kind in TRANSIENT_KINDS:
        flags |= FLAG_TRANSIENT
    if kind in STATUS_KINDS:
        flags |= FLAG_STATUS
    if kind in CONTEXT_KINDS:
        flags |= FLAG_CONTEXT
    if kind == "codex.thread.deleted":
        flags |= FLAG_DELETED
    return flags


class JournalIndex:
    """In-memory entries plus the file they describe."""

    def __init__(self, fingerprint=None):
        self.fingerprint = fingerprint
        self.sequences: list[int] = []
        self.offsets: list[int] = []
        self.lengths: list[int] = []
        self.flags: list[int] = []

    @property
    def committed(self):
        return self.sequences[-1] if self.sequences else 0

    @property
    def covered(self):
        if not self.offsets:
            return 0
        return self.offsets[-1] + self.lengths[-1]

    def add(self, sequence, offset, length, flags):
        self.sequences.append(sequence)
        self.offsets.append(offset)
        self.lengths.append(length)
        self.flags.append(flags)

    def scan(self, stream, session_id, *, end):
        """Index a fixed snapshot, validating complete records before publishing."""

        stream.seek(self.covered)
        for row, offset, length in iter_raw_records(
                stream, session_id, sequence=self.committed, checker=validate_state, end=end):
            self.add(row["sequence"], offset, length, flags_for(row["kind"]))
        return self

    @classmethod
    def read(cls, directory, stream, session_id):
        """Share one validated cache/rebuild policy between writers and readers."""
        stamp = fingerprint(stream.fileno())
        index, _exact = cls.load(directory, stamp)
        if index is not None:
            return index
        index = cls(stamp).scan(stream, session_id, end=stamp[2])
        index.save_current(stream.fileno(), directory)
        return index

    def select(self, flag, *, before=None):
        """Offsets and lengths of entries carrying ``flag``, oldest first."""

        selected = []
        for index, flags in enumerate(self.flags):
            if not flags & flag:
                continue
            if before is not None and self.offsets[index] >= before:
                break
            selected.append((self.offsets[index], self.lengths[index]))
        return selected

    def has(self, flag):
        return any(flags & flag for flags in self.flags)

    def find_start(self, messages):
        """First sequence/offset of the newest bounded chat window.

        Mirrors ``SessionJournal.replay_start`` beat for beat: walk back over
        the chat boundaries inside ``REPLAY_WINDOW_ROWS``, stop at the task
        that owns the oldest message, and when the bound cuts the walk fall
        back to the newest task start inside it or to the bound itself.
        """

        total = len(self.sequences)
        if not total:
            return 1, 0
        floor = max(0, total - replay_window_rows())
        index, boundaries = total, 0
        while index > floor and boundaries < messages:
            index -= 1
            if self.flags[index] & FLAG_CHAT:
                boundaries += 1
        if index == 0 and boundaries < messages:
            return self.sequences[0], self.offsets[0]
        while index > floor and not self.flags[index] & FLAG_TASK:
            index -= 1
        if floor > 0 and not self.flags[index] & FLAG_TASK:
            for probe in range(total - 1, index - 1, -1):
                if self.flags[probe] & FLAG_TASK:
                    index = probe
                    break
        return self.sequences[index], self.offsets[index]

    def find_task_start(self):
        """Sequence/offset of the newest task; the first screen of a session."""

        for index in range(len(self.sequences) - 1, -1, -1):
            if self.flags[index] & FLAG_TASK:
                return self.sequences[index], self.offsets[index]
        return (self.sequences[0] if self.sequences else 1), 0

    # --- persistence ------------------------------------------------------
    def save(self, directory):
        """Atomically refresh the cache; a failure only costs a rescan."""
        return save_index(self, directory)

    def save_current(self, fd, directory):
        """Publish only while the journal still matches the indexed snapshot."""
        try:
            if fingerprint(fd) == self.fingerprint:
                return self.save(directory)
        except OSError:
            pass
        return False

    @classmethod
    def load(cls, directory, fingerprint):
        """Return ``(index, True)`` for an intact exact cache, else ``(None, False)``."""
        return load_index(cls, directory, fingerprint)


def read_records(path, entries, *, strict=True):
    """Read the selected ``(offset, length)`` records with one open handle."""

    parse = strict_json if strict else json.loads
    rows = []
    fd = open_private(Path(path), os.O_RDONLY)
    try:
        for offset, length in entries:
            raw = os.pread(fd, length, offset)
            if len(raw) != length or not raw.endswith(b"\n"):
                raise ValueError("会话记录索引与日志不一致")
            rows.append(parse(raw))
    finally:
        os.close(fd)
    return rows


__all__ = (
    "CHAT_KINDS", "CONTEXT_KINDS", "FLAG_CHAT", "FLAG_CONTEXT", "FLAG_DELETED",
    "FLAG_METADATA", "FLAG_ORIGIN", "FLAG_TASK", "FLAG_TRANSIENT", "INDEX_CONTRACT",
    "INDEX_NAME", "JournalIndex", "METADATA_KINDS", "TRANSIENT_KINDS",
    "flags_for", "read_records",
)
