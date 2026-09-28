"""cad_ai_transport.v1: 4-byte big-endian byte length followed by strict UTF-8 JSON."""

from __future__ import annotations

import json
import select
import socket
import struct
import threading
import time
from concurrent.futures import CancelledError

from cadai.json_values import ProtocolError, strict_json

VERSION = "cad_ai_transport.v1"
MAX_FRAME = 1024 * 1024


class SendTimeout(TimeoutError):
    """A bounded send could have written a partial frame."""

    def __init__(self, sent):
        self.sent = sent
        super().__init__("Transport send timed out")


class SendError(OSError):
    """A bounded send failed after a known number of frame bytes."""

    def __init__(self, sent, error):
        self.sent = sent
        super().__init__(*error.args)


def encode(message: dict, max_frame: int = MAX_FRAME) -> bytes:
    if not isinstance(message, dict):
        raise ProtocolError("Message must be an object")
    body = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if not 0 < len(body) <= max_frame:
        raise ProtocolError("Frame size outside limit")
    return struct.pack("!I", len(body)) + body


class FrameDecoder:
    def __init__(self, max_frame: int = MAX_FRAME):
        self.buffer = bytearray()
        self.max_frame = max_frame
        self.failed = False

    def feed(self, data: bytes) -> list[dict]:
        if self.failed:
            raise ProtocolError("Decoder is unusable after protocol failure")
        self.buffer.extend(data)
        result = []
        try:
            while len(self.buffer) >= 4:
                size = struct.unpack("!I", self.buffer[:4])[0]
                if not 0 < size <= self.max_frame:
                    raise ProtocolError("Frame size outside limit")
                if len(self.buffer) < 4 + size:
                    break
                result.append(strict_json(bytes(self.buffer[4 : 4 + size])))
                del self.buffer[: 4 + size]
        except (ValueError, UnicodeError):
            self.failed = True
            raise
        return result

    def eof(self) -> None:
        if self.buffer:
            raise ProtocolError("Truncated frame at EOF")


class Connection:
    def __init__(self, sock: socket.socket, *, max_frame: int = MAX_FRAME):
        self.socket = sock
        self.max_frame = max_frame
        self._write_lock = threading.Lock()

    def send(self, message: dict, *, deadline=None, cancelled=None) -> None:
        framed = encode(message, self.max_frame)
        self._acquire_writer(deadline, cancelled)
        try:
            if deadline is None and cancelled is None:
                self.socket.sendall(framed)
                return
            sent = 0
            previous_timeout = self.socket.gettimeout()
            try:
                self.socket.setblocking(False)
                while sent < len(framed):
                    if cancelled is not None and cancelled():
                        raise CancelledError()
                    remaining = deadline - time.monotonic() if deadline is not None else None
                    if remaining is not None and remaining <= 0:
                        raise SendTimeout(sent)
                    wait = 0.1 if remaining is None else min(0.1, remaining)
                    if not select.select([], [self.socket], [], wait)[1]:
                        continue
                    try:
                        count = self.socket.send(framed[sent:])
                        if count == 0:
                            raise BrokenPipeError("Peer disconnected during send")
                        sent += count
                    except BlockingIOError:
                        continue
            except SendTimeout:
                raise
            except OSError as exc:
                raise SendError(sent, exc) from exc
            finally:
                try:
                    self.socket.settimeout(previous_timeout)
                except OSError:
                    pass
        finally:
            self._write_lock.release()

    def _acquire_writer(self, deadline, cancelled):
        if deadline is None and cancelled is None:
            self._write_lock.acquire()
            return
        while True:
            if cancelled is not None and cancelled():
                raise CancelledError()
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise SendTimeout(0)
            wait = 0.1 if remaining is None else min(0.1, remaining)
            if self._write_lock.acquire(timeout=wait):
                return

    def _read(self, size: int, *, deadline=None, cancelled=None) -> bytes:
        result = bytearray()
        while len(result) < size:
            if cancelled is not None and cancelled():
                raise CancelledError()
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Bridge response timed out")
                if not select.select([self.socket], [], [], min(0.1, remaining))[0]:
                    continue
            elif cancelled is not None:
                if not select.select([self.socket], [], [], 0.1)[0]:
                    continue
            data = self.socket.recv(size - len(result))
            if not data:
                if result:
                    raise ProtocolError("Truncated frame at EOF")
                raise EOFError("Peer disconnected during frame")
            result.extend(data)
        return bytes(result)

    def receive(self, *, deadline=None, cancelled=None) -> dict:
        size = struct.unpack("!I", self._read(4, deadline=deadline, cancelled=cancelled))[0]
        if not 0 < size <= self.max_frame:
            raise ProtocolError("Frame size outside limit")
        try:
            body = self._read(size, deadline=deadline, cancelled=cancelled)
        except EOFError as exc:
            raise ProtocolError("Truncated frame at EOF") from exc
        return strict_json(body)

    def close(self) -> None:
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.socket.close()
