"""Single owner of the current native thread identity and its transitions."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True)
class ThreadIdentity:
    thread_id: str | None = None
    history_mode: str | None = None


class ThreadBinding:
    def __init__(self, record=None, *, lock=None):
        record = record or {}
        self._identity = ThreadIdentity(record.get("thread_id"), record.get("history_mode"))
        self._lock = lock if lock is not None else RLock()

    @property
    def thread_id(self):
        with self._lock:
            return self._identity.thread_id

    @property
    def history_mode(self):
        with self._lock:
            return self._identity.history_mode

    @contextmanager
    def current(self):
        """Keep an ownership check and its local effect within one binding."""
        with self._lock:
            yield self._identity

    def accept(self, action, thread, *, expected, persist):
        """Publish a validated RPC result only after durable binding succeeds.

        The RPC runs outside this lock. Its expected parent is checked again
        here, so a delayed result cannot overwrite a newer binding.
        """
        with self._lock:
            if self._identity.thread_id != expected:
                raise ValueError("Thread identity changed before binding; no replay")
            thread_id = thread.get("id") if isinstance(thread, dict) else None
            if not isinstance(thread_id, str) or not thread_id.strip():
                raise ValueError("Invalid thread identity; no replay")
            valid = {"start": expected is None,
                     "resume": expected is not None and thread_id == expected,
                     "fork": expected is not None and thread_id != expected}
            if not valid.get(action, False):
                raise ValueError("Invalid " + str(action) + " identity; no replay")
            identity = ThreadIdentity(thread_id, thread.get("historyMode"))
            persist(identity)
            self._identity = identity
