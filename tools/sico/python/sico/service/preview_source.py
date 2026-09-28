"""Watermarked read-only history with bounded reads and no full-history cache."""

import os

from ..storage.journal import open_private
from .event_display import RAW_EVENTS, encoded

RAW_BYTES = 262144


class PreviewSource:
    def __init__(self, reader, journal=None):
        self.reader, self.journal = reader, journal
        self.session_id = reader.session_id
        self.committed = None
        self.fingerprint = None
        if journal is not None:
            with journal._events_lock:
                self.committed = len(journal.records)

    @staticmethod
    def _fingerprint(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def read(self, sequence, offset=0):
        if self.committed is not None and sequence >= self.committed:
            return [], offset
        limit = RAW_EVENTS if self.committed is None else min(RAW_EVENTS, self.committed - sequence)
        if self.journal is not None:
            events, _ = self.journal.committed_batch(sequence, limit, RAW_BYTES)
            events = [event for event in events if event["sequence"] <= self.committed]
            return events, offset
        fd = open_private(self.reader.directory / "events.jsonl", os.O_RDONLY)
        with os.fdopen(fd, "rb") as handle:
            fingerprint = self._fingerprint(os.fstat(handle.fileno()))
            if self.fingerprint is None:
                self.fingerprint = fingerprint
            if fingerprint != self.fingerprint:
                raise ValueError("历史文件已变化，请重新打开预览")
            handle.seek(offset)
            events, size = [], 0
            for event in self.reader.iter_events(handle, sequence=sequence):
                events.append(event)
                size += len(encoded(event))
                if len(events) >= limit or size >= RAW_BYTES:
                    break
            if self._fingerprint(os.fstat(handle.fileno())) != fingerprint:
                raise ValueError("历史文件在读取期间已变化，请重新打开预览")
            if not events and self.committed is not None and sequence < self.committed:
                raise ValueError("历史文件缺少已准备的记录")
            return events, handle.tell()

    def iter_events(self):
        sequence, offset = 0, 0
        while True:
            events, offset = self.read(sequence, offset)
            if not events:
                return
            yield from events
            sequence = events[-1]["sequence"]

    def event(self, sequence):
        if self.journal is not None:
            rows = self.journal.events(sequence - 1, limit=1)
            return rows[0] if rows and sequence <= self.committed else None
        return next((row for row in self.iter_events() if row["sequence"] == sequence), None)
