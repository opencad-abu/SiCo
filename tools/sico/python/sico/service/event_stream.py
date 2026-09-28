"""One bounded publication slot per active replay, owned by the event worker."""

import hashlib
import re
import threading
from collections import deque
from dataclasses import dataclass

from .event_content import detail_page
from .event_display import (
    BATCH_BYTES,
    BATCH_EVENTS,
    RAW_EVENTS,
    EventDisplay,
    coalesce,
    encoded,
)
from .published import freeze
from .state_publication import StatePublication

DISPLAY_CONTRACT = "agent_display.v1"
RAW_BYTES = 262144


@dataclass(frozen=True)
class DisplayBatch:
    identity: tuple
    activation: int
    start: int
    end: int
    committed: int
    events: tuple
    snapshot: object = None
    size: int = 0
    contract: str = DISPLAY_CONTRACT
    error: str = ""


class EventStream:
    def __init__(self, controller, cursor, activation, committed, *, reader=None, events=None,
                 preview_offset=0):
        self.controller, self.cursor = controller, cursor
        self.sessions, self.preview_owner = None, None
        self.identity, self.activation = cursor.identity, activation
        self.committed = committed
        self.reader, self.events = reader, events
        # Preview streams may start at a tail boundary; the offset points at
        # the first event that will be read from the closed journal file.
        self._preview_offset = preview_offset
        self.version = -1
        self.display = EventDisplay()
        self._pending = deque()
        self._pending_end = cursor.sequence
        self._snapshot = None
        self.publisher = StatePublication(controller) if controller is not None else None
        self._lock = threading.Lock()
        self._future = None
        self._closed = False
        self.finished = False

    def request(self, service):
        with self._lock:
            if self._closed:
                raise ValueError("Event stream is closed")
            if self._future is None:
                self._future = service._request(self._publish)
            return self._future

    def received(self, future):
        with self._lock:
            if self._future is not future:
                raise ValueError("Event publication receipt was replaced")
            self._future = None

    def close(self):
        with self._lock:
            self._closed = True
            future = self._future
            self._future = None
        if future is not None:
            future.cancel()

    def retire(self):
        """Only the event worker drops cumulative indexes and pending projections."""
        self.cursor = self.publisher = self._snapshot = None
        self._pending.clear()

    @property
    def session_id(self):
        return self.identity[0]

    def validate_current(self):
        from .replay import connection

        if self._closed:
            raise ValueError("Event stream is closed")
        if self.reader is not None:
            from .damaged_record import require_readable

            require_readable(self.reader)
        owner = self.controller
        if self.sessions is not None:
            expected = owner if owner is not None else self.preview_owner
            if self.sessions.controllers.get(self.session_id) is not expected:
                raise ValueError("Event stream session was reopened")
        if owner is not None and (connection(owner) != self.identity or owner.closing
                                  or owner._shutdown.is_set()):
            raise ValueError("Event stream belongs to another runtime or connection")
        if self.preview_owner is not None and (
                self.preview_owner.runtime_id != self.identity[1]
                or self.preview_owner.closing or self.preview_owner._shutdown.is_set()):
            raise ValueError("Preview stream runtime was changed or closed")

    def iter_events(self):
        """Opaque watermarked preview source consumed only by the data worker."""
        for event in self.events.iter_events():
            self.validate_current()
            yield event

    def _publish(self):
        start = self._pending_end + 1
        try:
            result = self._next(start)
            if self._closed:
                self._pending.clear()
                self._snapshot = None
            return result
        except Exception as exc:
            return DisplayBatch(self.identity, self.activation, start, start - 1,
                                self.committed, (), error=str(exc))

    def _next(self, start):
        self.validate_current()
        if not self._pending:
            if self.events is None:
                batch = self.controller.read_updates(
                    self.cursor.sequence, limit=RAW_EVENTS, max_bytes=RAW_BYTES,
                    since_version=self.version, publisher=self.publisher,
                )
            else:
                events, self._preview_offset = self.events.read(
                    self.cursor.sequence, self._preview_offset,
                )
                batch = dict(zip(("session_id", "runtime_id", "instance_id", "generation"),
                                 self.identity))
                batch.update(contract="agent_session.v1", snapshot=None,
                             events=events,
                             committed_sequence=self.committed)
            accepted = self.cursor.consume(batch)
            self.committed = batch["committed_sequence"]
            self._snapshot = batch["snapshot"]
            for event in coalesce(accepted):
                projected = self.display.project(event)
                if projected is not None:
                    self._pending.append(projected)
        events, size = [], 0
        while self._pending and len(events) < BATCH_EVENTS:
            event, cost = self._pending[0]
            if events and size + cost > BATCH_BYTES:
                break
            self._pending.popleft()
            events.append(event)
            size += cost
        end = events[-1]["sequence"] if self._pending else self.cursor.sequence
        self._pending_end = end
        self.finished = self.events is not None and end == self.committed
        snapshot = None
        if not self._pending and self._snapshot is not None:
            snapshot = self._snapshot
            self.version = self._snapshot[0]["version"]
            self._snapshot = None
        return DisplayBatch(self.identity, self.activation, start, end, self.committed,
                            tuple(events), snapshot, size)

    def detail(self, key, offset=0, limit=4096):
        """Read a bounded page from the exact journal event, never another runtime."""
        self.validate_current()
        match = re.fullmatch(r"e([1-9][0-9]*)_([a-f0-9]{64})", key)
        if not match or type(offset) is not int or offset < 0 or not 0 < limit <= 8192:
            raise ValueError("Invalid event detail reference")
        sequence = int(match[1])
        if self.controller is not None:
            rows = self.controller.journal.events(sequence - 1, limit=1)
            raw = rows[0] if rows else None
        else:
            raw = self.events.event(sequence)
        if raw is None or hashlib.sha256(encoded(raw)).hexdigest() != match[2]:
            raise ValueError("Event detail no longer matches the journal")
        reader = self.controller.journal if self.controller is not None else self.reader
        page = detail_page(raw, offset, limit, reader=reader)
        self.validate_current()
        return freeze(page)
