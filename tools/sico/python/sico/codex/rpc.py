"""Bounded app-server stdio client; protocol stays independent of Qt and models."""

from __future__ import annotations

import json
import os
import queue
import select
import threading
import time
from collections import deque

from ..transport.framing import strict_json
from .local_process import LocalProcess

MAX_LINE = 16 * 1024 * 1024


class RpcError(RuntimeError):
    pass


class RpcRejected(RpcError):
    """A correlated error response, distinct from an uncertain transport failure."""


class AppServer:
    def __init__(self, command, environment, cwd):
        self.messages = queue.Queue(maxsize=4096)
        self.pending = deque()
        self.notification_handler = None
        self.counter = 0
        self.stopped = threading.Event()
        self._process = LocalProcess(command, environment, cwd)
        self.child = self._process.child
        self.reader = threading.Thread(target=self._read, name="copilot-codex-reader")
        self.reader.start()
        try:
            self.request(
                "initialize",
                {
                    "capabilities": {"experimentalApi": True},
                    "clientInfo": {
                        "name": "cad_copilot",
                        "title": "Silicon Copilot",
                        "version": "0.1.0",
                    },
                },
            )
            self.send({"method": "initialized", "params": {}})
        except BaseException:
            self.close()
            raise

    def _read(self):
        pending = bytearray()
        try:
            descriptor = self.child.stdout.fileno()
            reader = select.poll()
            reader.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR)
            while not self.stopped.is_set():
                # A partial frame or an inherited writer must not prevent close.
                if not reader.poll(100):
                    continue
                raw = os.read(descriptor, 65536)
                if not raw:
                    break
                pending.extend(raw)
                while b"\n" in pending and not self.stopped.is_set():
                    end = pending.index(b"\n") + 1
                    if end > MAX_LINE:
                        raise RpcError("Invalid app-server frame")
                    message = strict_json(pending[:end])
                    del pending[:end]
                    while not self.stopped.is_set():
                        try:
                            self.messages.put(message, timeout=0.1)
                            break
                        except queue.Full:
                            continue
                if len(pending) >= MAX_LINE:
                    raise RpcError("Invalid app-server frame")
        except (OSError, ValueError, RpcError):
            pass
        finally:
            self.stopped.set()

    def send(self, message):
        raw = (json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n").encode()
        if len(raw) > MAX_LINE or self.child.poll() is not None:
            raise RpcError("Codex app-server is unavailable")
        try:
            view = memoryview(raw)
            while view:
                written = self.child.stdin.write(view)
                if not written:
                    raise RpcError("Codex app-server pipe closed")
                view = view[written:]
            self.child.stdin.flush()
        except (OSError, ValueError):
            raise RpcError("Codex app-server pipe closed") from None

    def _next(self, timeout):
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            if self.stopped.is_set():
                raise RpcError("Codex app-server disconnected") from None
            raise

    def next(self, timeout=0.1):
        if self.pending:
            return self.pending.popleft()
        return self._next(timeout)

    def defer(self, messages):
        """Return inspected frames to the active consumer in their original order."""
        if len(messages) + len(self.pending) > 4096:
            raise RpcError("Codex notification buffer exceeded")
        self.pending.extendleft(reversed(messages))

    def _notification(self, message):
        return ("id" not in message and self.notification_handler is not None
                and self.notification_handler(message))

    def poll_notifications(self):
        """Consume handled metadata only; caller must own the idle RPC consumer."""
        retained = deque()
        message = None
        try:
            while self.pending:
                message = self.pending.popleft()
                if not self._notification(message):
                    retained.append(message)
                message = None
            for _ in range(self.messages.qsize()):
                if len(retained) >= 4096:
                    break
                try:
                    message = self.messages.get_nowait()
                except queue.Empty:
                    break
                if not self._notification(message):
                    retained.append(message)
                message = None
        finally:
            if message is not None:
                retained.append(message)
            retained.extend(self.pending)
            self.pending = retained

    def request(self, method, params, timeout=30):
        self.poll_notifications()
        self.counter += 1
        request_id = self.counter
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self._next(min(0.1, max(0.001, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    # Protocol messages may include upstream request details or secrets.
                    raise RpcRejected(
                        f"Codex {method} failed (code {message['error'].get('code')})"
                    )
                return message.get("result", {})
            try:
                if self._notification(message):
                    continue
            except Exception:
                self.pending.append(message)
                raise
            if len(self.pending) >= 4096:
                raise RpcError("Codex notification buffer exceeded")
            self.pending.append(message)
        raise RpcError(f"Codex {method} timed out")

    def poll_requests(self, handler):
        """Resolve supported idle requests; retain unrelated frames in their original order."""
        retained = deque()
        message = None
        try:
            remaining = len(self.pending) + self.messages.qsize()
            for _ in range(min(4096, remaining)):
                try:
                    message = self.pending.popleft() if self.pending else self.messages.get_nowait()
                except queue.Empty:
                    break
                if not ("id" in message and message.get("method") and handler(message)):
                    retained.append(message)
                message = None
        finally:
            if message is not None:
                retained.append(message)
            retained.extend(self.pending)
            self.pending = retained

    def close(self):
        self.stopped.set()
        self._process.close()
        self.reader.join(timeout=.5)
        if self.reader.is_alive():
            raise RpcError("Codex reader cleanup was not confirmed")
        self.child.stdout.close()
