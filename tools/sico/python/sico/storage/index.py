"""Sidecar offset index for one journal: sequence, offset, length, kind flags.

The index is a cache, never an authority: any fingerprint or count mismatch
marks it stale, and callers fall back to scanning the journal. Authoritative
reads still parse records (strictly) themselves; the index only says where a
record starts and what class of content it carries, which is enough to jump
straight to the tail window, the native origins or the catalog metadata
without replaying the whole prefix.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..transport.framing import strict_json
from .journal import open_private
from .native_origin import ORIGIN_EVENTS

INDEX_CONTRACT = "cadai.journal.index.v1"
INDEX_NAME = "events.idx"

# Content classes carried by one record, used to select without parsing.
FLAG_ORIGIN = 1        # native_origin.ORIGIN_EVENTS (origin state for a tail)
FLAG_METADATA = 2      # catalog metadata (thread names, children, deletion)
FLAG_CHAT = 4          # task.started / model.completed (viewer window)
FLAG_TASK = 8          # task.started (a window may only start here)
FLAG_TRANSIENT = 16    # terminal replay drops these after coalescing
FLAG_CONTEXT = 32      # carries the bound context for the session
FLAG_DELETED = 64      # codex.thread.deleted (terminal marker)

METADATA_KINDS = frozenset({
    "codex.thread", "codex.thread.name", "codex.thread.title",
    "codex.thread.deleted", "codex.child",
})
CHAT_KINDS = frozenset({"task.started", "model.completed"})
TRANSIENT_KINDS = frozenset({"model.delta", "model.status", "router.status"})
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

    def scan(self, reader, stream):
        """Index every complete record after the covered prefix (light parse)."""

        stream.seek(self.covered)
        for row, offset, length in reader.iter_raw_records(
                stream, sequence=self.committed, validate=False):
            self.add(row["sequence"], offset, length, flags_for(row["kind"]))
        return self

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
        """First sequence/offset of the newest ``messages`` chat window.

        Mirrors ``SessionJournal.replay_start``: walk back over the chat
        boundaries and stop at the task that owns the oldest message.
        """

        boundaries = 0
        for index in range(len(self.sequences) - 1, -1, -1):
            flags = self.flags[index]
            if not flags & FLAG_CHAT:
                continue
            boundaries += 1
            if boundaries >= messages and flags & FLAG_TASK:
                return self.sequences[index], self.offsets[index]
        return (self.sequences[0] if self.sequences else 1), 0

    def find_task_start(self):
        """Sequence/offset of the newest task; the first screen of a session."""

        for index in range(len(self.sequences) - 1, -1, -1):
            if self.flags[index] & FLAG_TASK:
                return self.sequences[index], self.offsets[index]
        return (self.sequences[0] if self.sequences else 1), 0

    # --- persistence ------------------------------------------------------
    def save(self, directory):
        """Atomically refresh the cache; a failure only costs a rescan."""

        if not self.fingerprint:
            return False
        stamp = " ".join(str(part) for part in self.fingerprint)
        lines = [f"{INDEX_CONTRACT} {stamp} {len(self.sequences)}\n"]
        lines.extend(
            f"{sequence} {offset} {length} {flags}\n"
            for sequence, offset, length, flags in zip(
                self.sequences, self.offsets, self.lengths, self.flags)
        )
        try:
            temporary = Path(directory) / (INDEX_NAME + ".tmp")
            fd = open_private(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY)
            with os.fdopen(fd, "w") as stream:
                stream.writelines(lines)
            os.replace(temporary, Path(directory) / INDEX_NAME)
        except (OSError, ValueError):
            return False
        return True

    @classmethod
    def load(cls, directory, fingerprint):
        """Return ``(index, exact)``; ``(None, False)`` when unusable.

        ``exact`` means the header matches the journal fingerprint and every
        record is indexed. Otherwise the returned index (when the device,
        inode and a growing size still match) covers only a prefix that the
        caller may extend; a different file yields ``None``.
        """

        try:
            fd = open_private(Path(directory) / INDEX_NAME, os.O_RDONLY)
        except (OSError, ValueError):
            return None, False
        index, header_fingerprint, count = cls(), None, None
        try:
            with os.fdopen(fd, "r") as stream:
                header = stream.readline()
                parts = header.split()
                if len(parts) != 7 or parts[0] != INDEX_CONTRACT:
                    return None, False
                header_fingerprint = tuple(int(part) for part in parts[1:6])
                count = int(parts[6])
                for line in stream:
                    fields = line.split()
                    if len(fields) != 4:
                        break
                    index.add(int(fields[0]), int(fields[1]), int(fields[2]), int(fields[3]))
        except (OSError, ValueError):
            return None, False
        if len(index.sequences) != count:
            return None, False
        index.fingerprint = header_fingerprint
        if header_fingerprint == fingerprint:
            return index, True
        if (header_fingerprint[:2] == fingerprint[:2]
                and header_fingerprint[2] < fingerprint[2]):
            return index, False
        return None, False


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
