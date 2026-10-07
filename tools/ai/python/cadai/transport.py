"""Request/response transport over Virtuoso ipcBeginProcess stdio."""

from __future__ import annotations

import os
import copy
import queue
import select
import sys
import threading
from collections.abc import Mapping
from typing import Any, BinaryIO

from .live_model import LiveModelProtocolError, parse_live_message, parse_live_status
from .protocol import JsonLineDecoder, ProtocolError, encode_line, require_response


class TransportClosed(RuntimeError):
    pass


class VirtuosoTransport:
    def __init__(self, reader: BinaryIO | None = None, writer: BinaryIO | None = None):
        self.reader = reader or sys.stdin.buffer
        self.writer = writer or sys.stdout.buffer
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[str, queue.Queue[dict[str, Any] | BaseException]] = {}
        # Terminal activation is a one-bit wakeup.  Live events are separate
        # because they carry bounded JSON objects and must not be consumed by
        # the terminal control loop.
        self._events: queue.Queue[str] = queue.Queue(maxsize=1)
        self._live_events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=128)
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        try:
            self._reader_fd: int | None = self.reader.fileno()
        except (AttributeError, OSError, ValueError):
            self._reader_fd = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._read_loop,
            name="virtuoso-jsonl",
            daemon=self._reader_fd is None,
        )
        self._thread.start()

    def request(self, payload: Mapping[str, Any], timeout: float) -> tuple[bool, dict[str, Any]]:
        request_id = payload.get("id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("outbound request requires a string id")
        waiter: queue.Queue[dict[str, Any] | BaseException] = queue.Queue(maxsize=1)
        with self._pending_lock:
            if self._closed.is_set():
                raise TransportClosed("cdns-ipc is closed")
            if request_id in self._pending:
                raise ValueError("duplicate pending request id")
            self._pending[request_id] = waiter
        try:
            self.send(payload)
            try:
                item = waiter.get(timeout=timeout)
            except queue.Empty as exc:
                raise TimeoutError(
                    "Virtuoso request timed out; execution status is unknown and was not retried"
                ) from exc
            if isinstance(item, BaseException):
                raise item
            _response_id, ok, detail = require_response(item)
            return ok, detail
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    @property
    def is_closed(self) -> bool:
        return self._closed.is_set()

    def wait_closed(self, timeout: float | None = None) -> bool:
        return self._closed.wait(timeout)

    def next_event(self, timeout: float | None = None) -> str | None:
        try:
            if timeout is None:
                return self._events.get()
            if timeout <= 0:
                return self._events.get_nowait()
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    def next_live_event(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Return one validated live-model event, or ``None`` on timeout."""
        try:
            if timeout is None:
                value = self._live_events.get()
            elif timeout <= 0:
                value = self._live_events.get_nowait()
            else:
                value = self._live_events.get(timeout=timeout)
        except queue.Empty:
            return None
        # The live payload contains nested target/view/result mappings.  A
        # shallow ``dict`` copy would let a caller mutate queue-owned state
        # (and subsequently alter the event observed by another consumer).
        return copy.deepcopy(value)

    def send(self, payload: Mapping[str, Any]) -> None:
        encoded = encode_line(payload)
        with self._write_lock:
            if self._closed.is_set():
                raise TransportClosed("cdns-ipc is closed")
            self.writer.write(encoded)
            self.writer.flush()

    def route_response(self, message: Mapping[str, Any]) -> bool:
        try:
            request_id, _ok, _detail = require_response(message)
        except ProtocolError:
            return False
        with self._pending_lock:
            waiter = self._pending.get(request_id)
        if waiter is None:
            return False
        try:
            waiter.put_nowait(copy.deepcopy(dict(message)))
        except queue.Full:
            return False
        return True

    def route_event(self, message: Mapping[str, Any]) -> bool:
        if set(message) == {"event"} and message.get("event") == "terminal.show":
            try:
                self._events.put_nowait("terminal.show")
            except queue.Full:
                pass
            return True
        event = message.get("event")
        if not isinstance(event, str) or not event.startswith("live_model."):
            return False
        try:
            checked = (
                parse_live_status(message)
                if event == "live_model.status"
                else parse_live_message(message)
            )
        except (LiveModelProtocolError, TypeError, ValueError):
            return False
        try:
            self._live_events.put_nowait(copy.deepcopy(checked))
        except queue.Full:
            # Source changes are explicitly coalescible.  Keep the newest
            # event so a busy controller cannot cause unbounded backpressure.
            try:
                self._live_events.get_nowait()
            except queue.Empty:
                pass
            try:
                self._live_events.put_nowait(copy.deepcopy(checked))
            except queue.Full:
                return False
        return True

    def close(self, reason: str = "cdns-ipc closed") -> None:
        if not self._closed.is_set():
            self._closed.set()
            with self._pending_lock:
                waiters = list(self._pending.values())
            for waiter in waiters:
                try:
                    waiter.put_nowait(TransportClosed(reason))
                except queue.Full:
                    pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    def _read(self, size: int) -> bytes:
        if self._reader_fd is None:
            read = getattr(self.reader, "read1", self.reader.read)
            return read(size)
        while not self._closed.is_set():
            try:
                ready, _writable, _exceptional = select.select([self._reader_fd], [], [], 0.1)
            except (OSError, ValueError):
                return b""
            if ready:
                try:
                    return os.read(self._reader_fd, size)
                except BlockingIOError:
                    continue
                except OSError:
                    return b""
        return b""

    def _read_loop(self) -> None:
        decoder = JsonLineDecoder()
        try:
            while not self._closed.is_set():
                data = self._read(65_536)
                if not data:
                    break
                for message in decoder.feed(data):
                    if not self.route_response(message):
                        self.route_event(message)
            decoder.finish()
        except (OSError, ProtocolError) as exc:
            self.close(str(exc))
            return
        self.close()


__all__ = ["TransportClosed", "VirtuosoTransport"]
