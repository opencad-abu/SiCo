"""Qt-independent duplicate guards that survive their originating page."""

from threading import Lock


class PendingCommands:
    def __init__(self):
        self._keys = set()
        self._lock = Lock()

    def begin(self, key):
        with self._lock:
            if key in self._keys:
                return False
            self._keys.add(key)
            return True

    def finish(self, key):
        with self._lock:
            self._keys.discard(key)

    def snapshot(self):
        with self._lock:
            return frozenset(self._keys)

    def __contains__(self, key):
        with self._lock:
            return key in self._keys

    def __bool__(self):
        with self._lock:
            return bool(self._keys)
