"""One cleanup attempt, bounded observation and retained late completion."""

import threading
import time

DEFAULT_CLEANUP_SECONDS = 2.0
SESSION_END_SECONDS = 8.0
SERVICE_EXIT_SECONDS = 4.0


class Deadline:
    def __init__(self, seconds):
        self.expires = time.monotonic() + max(0, seconds)

    def remaining(self):
        return max(0, self.expires - time.monotonic())


class CleanupTask:
    """Never abandon ownership just because a caller stops waiting."""

    def __init__(self, operation, name):
        self._operation, self._name = operation, name
        self._lock = threading.Lock()
        self._thread = None
        self.done = threading.Event()
        self.error = None

    def start(self):
        with self._lock:
            if self._thread is None:
                thread = threading.Thread(target=self._run, name=self._name, daemon=True)
                thread.start()
                self._thread = thread
        return self

    def _run(self):
        try:
            self._operation()
        except BaseException as exc:
            self.error = exc
        finally:
            self.done.set()

    def wait(self, timeout=DEFAULT_CLEANUP_SECONDS):
        if not self.done.wait(timeout):
            return False
        if self.error is not None:
            raise self.error
        return True
