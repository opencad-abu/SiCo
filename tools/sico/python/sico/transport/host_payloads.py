"""Bounded expiring bridge payload references; credentials exist only in memory."""

import threading
import time
import uuid

from ..core.contracts import json_copy
from ..service.host_contract import capture_payload, digest


class HostPayloads:
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._items = {}

    def _expire(self):
        now = self._clock()
        self._items = {key: row for key, row in self._items.items() if row[2] > now}

    def stage(self, owner, payload):
        payload = capture_payload(payload)
        fingerprint = digest(payload)
        with self._lock:
            self._expire()
            if len(self._items) >= 16:
                raise ValueError("Host payload capacity reached")
            key = uuid.uuid4().hex
            self._items[key] = (owner, payload, self._clock() + 120)
            return dict(payload_id=key, digest=fingerprint)

    def read(self, key):
        with self._lock:
            self._expire()
            row = self._items.get(key)
            if row is None:
                raise ValueError("Host payload expired or detached")
            return json_copy(row[1])

    def release(self, owner):
        with self._lock:
            self._items = {key: row for key, row in self._items.items() if row[0] is not owner}

    def release_all(self):
        with self._lock:
            self._items.clear()
