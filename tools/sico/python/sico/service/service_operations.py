"""Bounded stable operation results used for unknown-result reconciliation."""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from ..transport.framing import ProtocolError
from .service_protocol import exact_fields
from .service_status import ServiceStatus
from .service_values import json_view, name


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    method: str
    params: dict
    result: dict
    recorded_at: float

    def record(self):
        return dict(operation_id=self.operation_id, method=self.method,
                    params=json_view(self.params), result=json_view(self.result),
                    recorded_at=self.recorded_at)


@dataclass(frozen=True)
class OperationStatus:
    operation_id: str
    state: str
    method: str
    result: dict = None

    def __post_init__(self):
        name(self.operation_id)
        if self.state == "completed":
            result = ServiceStatus.from_record(self.result, self.method)
            object.__setattr__(self, "result", json_view(result.record()))
        elif self.state != "unknown" or self.method != "unknown" or self.result is not None:
            raise ProtocolError("Invalid operation status")

    def record(self):
        return dict(operation_id=self.operation_id, state=self.state, method=self.method,
                    result=None if self.result is None else json_view(self.result))

    @classmethod
    def from_record(cls, row):
        exact_fields(row, {"operation_id", "state", "method", "result"})
        return cls(**row)


class OperationStore:
    """Small process-local cache; durable business operation storage is AS-03."""

    def __init__(self, *, capacity=256, ttl=3600.0, clock=time.monotonic):
        if type(capacity) is not int or not 1 <= capacity <= 4096:
            raise ValueError("Invalid operation result capacity")
        if not isinstance(ttl, (int, float)) or isinstance(ttl, bool) or not 0 < ttl <= 86400:
            raise ValueError("Invalid operation result lifetime")
        self._capacity, self._ttl, self._clock = capacity, float(ttl), clock
        self._rows = OrderedDict()
        self._lock = threading.Lock()

    def record(self, operation_id, method, params, result):
        name(operation_id)
        if (not isinstance(method, str) or not isinstance(params, dict)
                or not isinstance(result, dict)):
            raise ProtocolError("Invalid operation result")
        row = OperationRecord(operation_id, method, json_view(params), json_view(result),
                              self._clock())
        with self._lock:
            self._purge(row.recorded_at)
            existing = self._rows.get(operation_id)
            if existing is not None and (existing.method != method
                                         or existing.params != row.params):
                raise ProtocolError("Operation identity was reused with different parameters")
            if existing is not None:
                return existing
            self._rows[operation_id] = row
            self._rows.move_to_end(operation_id)
            while len(self._rows) > self._capacity:
                self._rows.popitem(last=False)
        return row

    def get(self, operation_id):
        name(operation_id)
        with self._lock:
            self._purge(self._clock())
            row = self._rows.get(operation_id)
            if row is not None:
                self._rows.move_to_end(operation_id)
            return row

    def _purge(self, now):
        expired = [key for key, row in self._rows.items()
                   if now - row.recorded_at >= self._ttl]
        for key in expired:
            self._rows.pop(key, None)
