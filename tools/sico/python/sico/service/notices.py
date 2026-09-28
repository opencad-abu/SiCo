"""Bounded, ordered desktop notices with all encoding and pipe writes off Qt."""

from __future__ import annotations

import json
import logging
import os
import queue
import select
import threading
import time

from ..core.contracts import identifier


class HostNotices:
    FIELDS = {
        "ready": ("bridge_id", "router_id", "instance_id", "generation", "session_id", "target_id"),
        "shown": (), "hidden": (), "accepted": ("id",), "rejected": ("id",),
        "released": ("target_id",), "new_session": ("id",), "reconnect": ("id",),
    }

    def __init__(self, fd, *, capacity=64, write_timeout=5.0):
        if capacity <= 0 or write_timeout <= 0:
            raise ValueError("Host notice limits must be positive")
        self._queue = queue.Queue(maxsize=capacity)
        self._lock = threading.Lock()
        self._closing = False
        self._close_deadline = None
        self._write_timeout = write_timeout
        self.stalled = threading.Event()
        self.failed = threading.Event()
        self.error = ""
        self._fd = os.dup(fd)
        self._thread = threading.Thread(target=self._run, name="copilot-notices", daemon=True)
        try:
            self._thread.start()
        except BaseException:
            os.close(self._fd)
            raise

    def __call__(self, kind, **payload):
        # Only bounded scalar identities cross this queue; no mutable payload or
        # formatting work is retained from a Qt callback.
        fields = self.FIELDS.get(kind)
        if fields is None or set(payload) != set(fields):
            raise ValueError("Invalid desktop notice")
        message = {"kind": kind, **{key: identifier(payload[key]) for key in fields}}
        with self._lock:
            if self._closing or self.failed.is_set():
                return False
            try:
                self._queue.put_nowait(message)
            except queue.Full:
                self.error = "Desktop host notice queue is full"
                self.failed.set()
                return False
        return True

    def _check_deadline(self, deadline):
        if self.failed.is_set():
            return False
        with self._lock:
            close_deadline = self._close_deadline
        if close_deadline is not None and time.monotonic() >= close_deadline:
            raise TimeoutError("Desktop host notices could not drain before shutdown")
        if time.monotonic() >= deadline:
            if not self.stalled.is_set():
                self.stalled.set()
                logging.getLogger(__name__).warning(
                    "Desktop host notice delivery stalled; retaining queued receipts for retry"
                )
        return True

    def _write(self, data):
        deadline = time.monotonic() + self._write_timeout
        remaining = memoryview(data)
        while remaining and self._check_deadline(deadline):
            try:
                count = os.write(self._fd, remaining)
            except BlockingIOError:
                select.select([], [self._fd], [], 0.05)
                continue
            if count <= 0:
                raise OSError("Desktop host notice pipe made no progress")
            remaining = remaining[count:]
            deadline = time.monotonic() + self._write_timeout
        if not remaining and self.stalled.is_set():
            self.stalled.clear()
            logging.getLogger(__name__).info("Desktop host notice delivery resumed")

    def _run(self):
        blocking = None
        try:
            blocking = os.get_blocking(self._fd)
            os.set_blocking(self._fd, False)
            while not self.failed.is_set():
                try:
                    message = self._queue.get(timeout=0.05)
                except queue.Empty:
                    with self._lock:
                        if self._closing:
                            break
                    continue
                data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
                self._write(data)
        except Exception as exc:
            with self._lock:
                if not self.failed.is_set():
                    self.error = str(exc)
                    self.failed.set()
        finally:
            try:
                if blocking is not None:
                    os.set_blocking(self._fd, blocking)
            finally:
                os.close(self._fd)
            if self.failed.is_set():
                logging.getLogger(__name__).error(
                    "Desktop host notice channel failed: %s", self.error,
                )

    def close(self, timeout=1.0):
        """Stop accepting notices and allow a bounded background drain; never join Qt."""
        with self._lock:
            if not self._closing:
                self._closing = True
                self._close_deadline = time.monotonic() + max(0, timeout)

    def wait(self, timeout=None):
        self._thread.join(timeout)
        return not self._thread.is_alive()
