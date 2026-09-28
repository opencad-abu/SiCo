"""Non-blocking stdin/stdout transport for the Virtuoso JSONL bridge."""

from __future__ import annotations

import io
import os
import sys
from threading import Lock
from typing import Any

from PyQt5.QtCore import QObject, QSocketNotifier, pyqtSignal

from .protocol import MAX_LINE_BYTES, ProtocolError, decode_line, encode_message


class StdioBridge(QObject):
    requestReceived = pyqtSignal(dict)
    responseReceived = pyqtSignal(dict)
    eventReceived = pyqtSignal(dict)
    protocolError = pyqtSignal(object)
    disconnected = pyqtSignal()

    def __init__(
        self,
        *,
        reader=None,
        writer=None,
        install_notifier: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._reader = reader if reader is not None else sys.stdin.buffer
        if writer is None:
            self._writer = getattr(sys.stdout, "buffer", sys.stdout)
            self._binary_writer = self._writer is not sys.stdout
            if not self._binary_writer and hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="strict")
        else:
            self._writer = writer
            self._binary_writer = isinstance(
                writer, (io.BufferedIOBase, io.RawIOBase, io.BytesIO)
            ) or "b" in getattr(writer, "mode", "")
        self._buffer = bytearray()
        self._next_id = 1
        self._write_lock = Lock()
        self._notifier: QSocketNotifier | None = None
        self._fd: int | None = None
        self._was_blocking: bool | None = None
        if install_notifier:
            self._install_notifier()

    def _install_notifier(self) -> None:
        try:
            self._fd = self._reader.fileno()
            self._was_blocking = os.get_blocking(self._fd)
            os.set_blocking(self._fd, False)
        except (AttributeError, OSError, ValueError) as exc:
            raise RuntimeError("stdio bridge requires a file-backed stdin") from exc
        self._notifier = QSocketNotifier(self._fd, QSocketNotifier.Read, self)
        self._notifier.activated.connect(self._read_ready)

    def close(self) -> None:
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None
        if self._fd is not None and self._was_blocking is not None:
            try:
                os.set_blocking(self._fd, self._was_blocking)
            except OSError:
                pass
        self._fd = None

    def request(self, method: str, params: dict[str, Any] | None = None) -> int:
        request_id = self._next_id
        self._next_id += 1
        self._send({"id": request_id, "method": method, "params": params or {}})
        return request_id

    def respond(
        self,
        request_id: int,
        *,
        result: Any | None = None,
        error: dict[str, str] | None = None,
    ) -> None:
        if error is None:
            self._send({"id": request_id, "ok": True, "result": result or {}})
        else:
            self._send({"id": request_id, "ok": False, "error": error})

    def feed_data(self, data: bytes) -> None:
        self._buffer.extend(data)
        if len(self._buffer) > MAX_LINE_BYTES and b"\n" not in self._buffer:
            self._buffer.clear()
            self.protocolError.emit(
                ProtocolError("line_too_long", "JSONL message exceeds 65536 bytes")
            )
            return
        while b"\n" in self._buffer:
            raw, _, remainder = self._buffer.partition(b"\n")
            self._buffer = bytearray(remainder)
            if raw.strip():
                self.feed_line(raw)

    def feed_line(self, line: bytes | str) -> None:
        try:
            message = decode_line(line)
        except ProtocolError as exc:
            self.protocolError.emit(exc)
            if exc.request_id is not None:
                self.respond(exc.request_id, error=exc.to_dict())
            return
        if "method" in message:
            if message["method"] == "bridge.ping":
                self.respond(message["id"], result={"alive": True})
            elif message["method"] == "gui.select_net":
                self.requestReceived.emit(message)
            else:
                self.respond(
                    message["id"],
                    error={"code": "wrong_direction", "message": "method is GUI outbound"},
                )
        elif "event" in message:
            self.eventReceived.emit(message)
        else:
            self.responseReceived.emit(message)

    def _read_ready(self, _fd: int | None = None) -> None:
        if self._fd is None:
            return
        try:
            data = os.read(self._fd, 65_536)
        except BlockingIOError:
            return
        except OSError as exc:
            self.protocolError.emit(str(exc))
            self.close()
            self.disconnected.emit()
            return
        if not data:
            self.close()
            self.disconnected.emit()
            return
        self.feed_data(data)

    def _send(self, payload: dict[str, Any]) -> None:
        text = encode_message(payload)
        with self._write_lock:
            self._writer.write(text.encode("utf-8") if self._binary_writer else text)
            self._writer.flush()


__all__ = ["StdioBridge"]
