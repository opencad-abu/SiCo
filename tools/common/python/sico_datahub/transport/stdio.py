"""Newline-delimited canonical JSON over binary standard streams."""

from __future__ import annotations

import select
from typing import Optional

from ..codec import decode_line, encode_line
from ..envelope import Envelope
from ..errors import HubError
from ..limits import MAX_ENVELOPE_BYTES
from .base import Transport, TransportClosed, require_envelope


class StdioTransport(Transport):
    def __init__(
        self,
        reader,
        writer,
        *,
        identity="stdio",
        max_bytes=MAX_ENVELOPE_BYTES,
        close_streams=False,
    ):
        self._reader = reader
        self._writer = writer
        self._identity = identity
        self._max_bytes = max_bytes
        self._close_streams = close_streams
        self._closed = False

    def send(self, envelope: Envelope) -> None:
        if self._closed:
            raise TransportClosed("Transport is closed")
        frame = encode_line(require_envelope(envelope))
        if len(frame) > self._max_bytes + 1:
            raise HubError("result_too_large", "Line frame exceeds its byte limit")
        try:
            self._writer.write(frame)
            self._writer.flush()
        except (BrokenPipeError, OSError) as exc:
            raise TransportClosed("Transport write failed") from exc

    def receive(self, deadline: Optional[float] = None) -> Envelope:
        if self._closed:
            raise TransportClosed("Transport is closed")
        self._wait(deadline)
        try:
            frame = self._reader.readline(self._max_bytes + 2)
        except (OSError, ValueError) as exc:
            raise TransportClosed("Transport read failed") from exc
        if not frame:
            raise TransportClosed("Peer closed the stream")
        if (
            len(frame) > self._max_bytes + 1
            or not frame.endswith(b"\n")
            or b"\n" in frame[:-1]
        ):
            raise HubError("invalid_arguments", "Invalid or oversized line frame")
        return decode_line(frame)

    def _wait(self, deadline):
        if deadline is None:
            return
        remaining = self.timeout(deadline)
        try:
            descriptor = self._reader.fileno()
        except (AttributeError, OSError, ValueError):
            return
        if not select.select([descriptor], [], [], remaining)[0]:
            raise HubError("timeout", "Stdio receive timed out")

    def close(self, reason: str = "") -> None:
        if self._closed:
            return
        self._closed = True
        if not self._close_streams:
            return
        for stream in (self._writer, self._reader):
            try:
                stream.close()
            except (AttributeError, OSError):
                pass

    def identity(self) -> str:
        return self._identity


__all__ = ["StdioTransport"]
