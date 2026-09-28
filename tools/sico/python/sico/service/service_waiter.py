"""Cancellable background result publication shared by service startup and administration."""

import math
import threading
import time
from concurrent.futures import CancelledError, Future, InvalidStateError


class ServiceWaiter:
    def __init__(self, operation, *, timeout, finished=None):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Service request timeout must be finite and positive")
        self.ready = Future()
        self._wake = threading.Event()
        self.ready.add_done_callback(lambda _future: self._wake.set())
        self._thread = threading.Thread(target=self._run, args=(operation, timeout, finished),
                                        name="project-service-request", daemon=True)
        self._thread.start()

    def close(self):
        self.ready.cancel()

    def wait(self, timeout=None):
        """Backend/tests only; Qt must poll ready instead of joining a worker."""
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _cancelled(self):
        return self.ready.cancelled()

    def _check(self, deadline):
        if self._cancelled():
            raise CancelledError()
        if time.monotonic() >= deadline:
            raise TimeoutError("Project service wait timed out; service may still be running")

    def _run(self, operation, timeout, finished):
        try:
            self._publish(operation, timeout)
        finally:
            if finished is not None:
                finished(self)

    def _publish(self, operation, timeout):
        try:
            deadline = time.monotonic() + timeout
            result = operation(deadline)
            self._check(deadline)
        except CancelledError:
            self.ready.cancel()
        except Exception as exc:
            try:
                self.ready.set_exception(exc)
            except InvalidStateError:
                pass
        else:
            try:
                self.ready.set_result(result)
            except InvalidStateError:
                pass
