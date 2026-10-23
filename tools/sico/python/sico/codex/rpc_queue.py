"""Byte- and count-bounded FIFO for Codex app-server messages."""

from __future__ import annotations

import json
import queue
import time
from collections import deque
from collections import namedtuple

_Message = namedtuple("_Message", "value size")
MAX_RPC_QUEUE_BYTES = 64 * 1024 * 1024
MAX_RPC_PENDING_BYTES = 64 * 1024 * 1024


def message_size(value):
    return len(json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    ).encode("utf-8"))


class BoundedMessageQueue(queue.Queue):
    """A queue whose memory admission includes frame bytes, not only item count.

    A single oversized frame is admitted when the queue is empty so callers can
    still observe or reject it with their protocol limit. Otherwise producers
    wait for consumers to release both item and byte capacity.
    """

    def __init__(self, maxsize, max_bytes):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("Message queue byte budget must be positive")
        self.max_bytes = max_bytes
        super().__init__(maxsize=maxsize)

    def _init(self, maxsize):
        super()._init(maxsize)
        self._bytes = 0

    @property
    def queued_bytes(self):
        with self.mutex:
            return self._bytes

    def full(self):
        with self.mutex:
            return ((self.maxsize > 0 and self._qsize() >= self.maxsize)
                    or self._bytes >= self.max_bytes)

    def put(self, item, block=True, timeout=None):
        self.put_sized(item, message_size(item), block=block, timeout=timeout)

    def put_sized(self, item, size, block=True, timeout=None):
        if type(size) is not int or size < 0:
            raise ValueError("Message size must be a nonnegative integer")
        with self.not_full:
            if not block:
                if self._full(size):
                    raise queue.Full
            elif timeout is None:
                while self._full(size):
                    self.not_full.wait()
            elif timeout < 0:
                raise ValueError("'timeout' must be a non-negative number")
            else:
                deadline = time.monotonic() + timeout
                while self._full(size):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Full
                    self.not_full.wait(remaining)
            self.queue.append(_Message(item, size))
            self._bytes += size
            self.unfinished_tasks += 1
            self.not_empty.notify()

    def _full(self, size=0):
        return ((self.maxsize > 0 and self._qsize() >= self.maxsize)
                or (self._bytes > 0 and self._bytes + size > self.max_bytes))

    def _get(self):
        message = super()._get()
        self._bytes -= message.size
        return message.value


class BoundedMessageDeque:
    """Byte-bounded deferred-message storage for the RPC consumer."""

    def __init__(self, max_bytes, values=()):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("Message pending byte budget must be positive")
        self.max_bytes = max_bytes
        self._items = deque()
        self._bytes = 0
        self.extend(values)

    @property
    def queued_bytes(self):
        return self._bytes

    def _admit(self, value):
        size = message_size(value)
        if size > self.max_bytes or self._bytes + size > self.max_bytes:
            raise queue.Full
        return _Message(value, size)

    def append(self, value):
        message = self._admit(value)
        self._items.append(message)
        self._bytes += message.size

    def appendleft(self, value):
        message = self._admit(value)
        self._items.appendleft(message)
        self._bytes += message.size

    def extend(self, values):
        items = [_Message(value, message_size(value)) for value in values]
        size = sum(message.size for message in items)
        if size > self.max_bytes or self._bytes + size > self.max_bytes:
            raise queue.Full
        self._items.extend(items)
        self._bytes += size

    def extendleft(self, values):
        items = [_Message(value, message_size(value)) for value in values]
        size = sum(message.size for message in items)
        if size > self.max_bytes or self._bytes + size > self.max_bytes:
            raise queue.Full
        self._items.extendleft(items)
        self._bytes += size

    def popleft(self):
        message = self._items.popleft()
        self._bytes -= message.size
        return message.value

    def clear(self):
        self._items.clear()
        self._bytes = 0

    def __len__(self):
        return len(self._items)

    def __bool__(self):
        return bool(self._items)

    def __iter__(self):
        return (message.value for message in self._items)


class RpcPendingBuffer:
    """Deferred-message operations shared by the RPC consumer."""

    def _init_pending(self):
        self.pending = BoundedMessageDeque(MAX_RPC_PENDING_BYTES)

    def next(self, timeout=0.1):
        if self.pending:
            return self.pending.popleft()
        return self._next(timeout)

    def defer(self, messages):
        limit = getattr(self, "_buffer_limit", 4096)
        if len(messages) + len(self.pending) > limit:
            self._buffer_exceeded()
        try:
            self.pending.extendleft(reversed(messages))
        except queue.Full:
            self._buffer_exceeded()

    def _retain_pending(self, message):
        try:
            self.pending.append(message)
        except queue.Full:
            self._buffer_exceeded()

    def _replace_pending(self, values):
        try:
            self.pending = BoundedMessageDeque(MAX_RPC_PENDING_BYTES, values)
        except queue.Full:
            self._buffer_exceeded()
