"""Client replay capabilities contain wire identities and local publication state only."""

from dataclasses import fields

from ..transport.framing import ProtocolError
from .event_display import BATCH_BYTES, BATCH_EVENTS
from .event_stream import DISPLAY_CONTRACT, DisplayBatch
from .remote_validation import display_batch
from .replay_view import CursorPosition, ReplayView
from .service_protocol import exact_fields
from .service_session_dto import ReplayCursor


class RemoteFeed:
    def __init__(self, api, cursor, epoch):
        self._api, self._wire_cursor, self._epoch = api, cursor, epoch
        self.identity, self.activation = cursor.identity, cursor.activation
        self.cursor = CursorPosition(cursor.sequence)
        self._closed = False
        self._sequence, self._committed, self._version = cursor.sequence, cursor.committed, -1
        self._receipt = None

    def close(self):
        if self._closed:
            return
        self._closed = True
        if not self._api.detached and self._epoch == self._api._events.epoch:
            try:
                self._api._events.request("close", {"cursor": self._wire_cursor.record()})
            except (RuntimeError, ValueError):
                self._api._events.retire_connection()

    def validate_current(self):
        current = self._api.session(self.identity[0])
        retired_history = (self._wire_cursor.readonly and self.identity[1] is None
                           and (current is None or self._api.is_closing(current)))
        if (self._closed or self._api.detached or self._api._events.retiring.is_set()
                or self._epoch != self._api._events.epoch
                or (not retired_history and current is not None
                    and current.runtime_id != self.identity[1])
                or (not self._wire_cursor.readonly and not self._api.owns(current))):
            raise ValueError("回放连接已失效，请重新打开页面")

    def batch(self):
        self.validate_current()
        if self._receipt is not None:
            raise ValueError("Previous event batch has not been consumed")
        self._receipt = self._api._events.request("batch", {"cursor": self._wire_cursor.record()},
                                                self._publish, validate=self.validate_current)
        return self._receipt

    def received(self, receipt):
        if self._receipt is not receipt:
            raise ValueError("Event receipt belongs to another subscription")
        self._receipt = None

    def _publish(self, row):
        exact_fields(row, {field.name for field in fields(DisplayBatch)})
        if (not isinstance(row["identity"], (tuple, list))
                or not isinstance(row["events"], (tuple, list))):
            raise ProtocolError("Invalid replay publication")
        batch = DisplayBatch(**dict(row, identity=tuple(row["identity"]),
                                    events=tuple(row["events"])))
        display_batch(batch, self.identity)
        if (batch.contract != DISPLAY_CONTRACT or batch.identity != self.identity
                or batch.activation != self.activation or batch.start != self._sequence + 1
                or batch.end < self._sequence or batch.end > batch.committed
                or batch.committed < self._committed or batch.size > BATCH_BYTES
                or len(batch.events) > BATCH_EVENTS
                or (self._wire_cursor.readonly and batch.committed != self._committed)):
            raise ProtocolError("Frontend event identity or range mismatch")
        if batch.snapshot is not None:
            state = batch.snapshot[0]
            if state["sequence"] > batch.end or state["version"] < self._version:
                raise ProtocolError("Snapshot crossed its consumed event watermark")
            self._version = state["version"]
        self._sequence, self._committed = batch.end, batch.committed
        self.validate_current()
        return batch


def publish_replay(api, expected, row):
    exact_fields(row, {"cursor", "context"})
    cursor = ReplayCursor.from_record(row["cursor"])
    if (cursor.address.project_id != api.descriptor.project_id
            or cursor.address.service_id != api.descriptor.service_id
            or cursor.connection_id != api._events.connection_id
            or cursor.address.session.session_id != expected["session_id"]
            or cursor.address.session.runtime_id != expected["runtime_id"]
            or cursor.activation != expected["activation"]
            or cursor.readonly != expected["readonly"]
            or (expected["resume"] is not None
                and cursor.sequence != expected["resume"]["sequence"])):
        raise ProtocolError("Replay belongs to another request")
    return ReplayView(RemoteFeed(api, cursor, api._events.epoch), cursor.committed, row["context"])
