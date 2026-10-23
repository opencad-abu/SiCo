"""Serialize nonblocking pipe frames within one deadline, without worker threads."""

import math
import os
import select
import threading
import time

QUANTUM = .05


class WriteFailure(RuntimeError):
    def __init__(self, reason, phase, sent=0):
        super().__init__(reason)
        self.phase, self.sent = phase, sent


def deadline_after(timeout):
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout < 0):
        raise ValueError("RPC timeout must be finite and nonnegative")
    return time.monotonic() + timeout


def remaining(deadline, stopped, cancelled, phase, sent=0):
    if stopped is not None and stopped.is_set():
        raise WriteFailure("disconnected", phase, sent)
    if cancelled is not None and cancelled():
        raise WriteFailure("cancelled", phase, sent)
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise WriteFailure("timed out", phase, sent)
    return min(seconds, QUANTUM)


def acquire(lock, deadline, stopped, cancelled, phase):
    while not lock.acquire(timeout=remaining(deadline, stopped, cancelled, phase)):
        pass


class PipeWriter:
    def __init__(self, stream, stopped):
        self.stream, self.stopped = stream, stopped
        self._lock = threading.Lock()
        self._descriptor = stream.fileno()
        os.set_blocking(self._descriptor, False)

    def write(self, raw, deadline, cancelled):
        acquire(self._lock, deadline, self.stopped, cancelled, "writer")
        sent = 0
        try:
            poller = select.poll()
            poller.register(self._descriptor, select.POLLOUT | select.POLLHUP | select.POLLERR)
            while sent < len(raw):
                wait = remaining(deadline, self.stopped, cancelled, "write", sent)
                try:
                    size = os.write(self._descriptor, memoryview(raw)[sent:])
                    if not size:
                        raise OSError("closed pipe")
                    sent += size
                except InterruptedError:
                    continue
                except BlockingIOError:
                    poller.poll(max(1, math.ceil(wait * 1000)))
        except OSError:
            self.stopped.set()
            raise WriteFailure("pipe closed", "write", sent) from None
        except BaseException:
            if sent:
                # A truncated JSON line must never be followed by another frame.
                self.stopped.set()
            raise
        finally:
            self._lock.release()

    def close(self):
        self.stopped.set()
        # The only holder performs nonblocking writes and checks stop every 50 ms.
        with self._lock:
            self.stream.close()
