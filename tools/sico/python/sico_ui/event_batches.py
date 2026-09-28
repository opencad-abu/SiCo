"""Lightweight range validation and time-sliced application of published batches."""

import time
from dataclasses import dataclass

from sico.service.event_display import BATCH_BYTES, BATCH_EVENTS, coalesce
from sico.service.event_stream import DISPLAY_CONTRACT

from .receipts import completed_result


@dataclass
class DisplayCursor:
    identity: tuple
    sequence: int


class BatchDelivery:
    BUDGET = 0.008
    ITEMS = 16
    # While a replay is still catching up, apply far more events per tick so a
    # long tail is not metered out 16 events at a time; the 8 ms budget above
    # still bounds each call, and the steady-state cadence stays at ITEMS.
    CATCH_UP_ITEMS = 128

    def __init__(self, worker, stream):
        self.worker, self.stream = worker, stream
        self.cursor = DisplayCursor(stream.identity, stream.cursor.sequence)
        self.future = None
        self.batch = None
        self.offset = 0
        self.closed = False
        self.caught_up = False

    def close(self):
        self.closed = True
        self.stream.close()
        self.future, self.batch = None, None

    def request(self):
        if not self.closed and self.future is None and self.batch is None:
            self.future = self.worker.event_batch(self.stream)

    def take(self):
        self.request()
        if self.future is None or not self.future.done():
            return False
        future, self.future = self.future, None
        self.stream.received(future)
        # The Future is completed. No worker wait or journal access occurs here.
        batch = completed_result(future)
        if (batch.contract != DISPLAY_CONTRACT or batch.identity != self.cursor.identity
                or batch.activation != self.stream.activation
                or batch.start != self.cursor.sequence + 1 or batch.end < batch.start - 1
                or batch.end > batch.committed or batch.size > BATCH_BYTES
                or len(batch.events) > BATCH_EVENTS):
            raise ValueError("Published event batch identity or sequence mismatch")
        if batch.error:
            raise ValueError(batch.error)
        previous = batch.start - 1
        for event in batch.events:
            if (event["session_id"] != batch.identity[0]
                    or not previous < event["sequence"] <= batch.end):
                raise ValueError("Published event range mismatch")
            previous = event["sequence"]
        if batch.snapshot is not None and batch.snapshot[0]["sequence"] > batch.end:
            raise ValueError("Published snapshot is ahead of its events")
        self.batch, self.offset = batch, 0
        return True

    def apply(self, receive, snapshot):
        if self.closed or (self.batch is None and not self.take()):
            return False
        self.stream.validate_current()
        deadline = time.monotonic() + self.BUDGET
        budget = self.ITEMS if self.caught_up else self.CATCH_UP_ITEMS
        count = 0
        while self.offset < len(self.batch.events):
            receive(self.batch.events[self.offset])
            self.offset += 1
            count += 1
            if count >= budget or time.monotonic() >= deadline:
                return False
        batch, self.batch = self.batch, None
        self.cursor.sequence = batch.end
        if batch.snapshot is not None:
            snapshot(*batch.snapshot)
        self.caught_up = batch.end == batch.committed
        if not self.caught_up:
            self.request()
        return True


__all__ = ("BatchDelivery", "coalesce")
