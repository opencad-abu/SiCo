"""Bounded SKILL stdio reader lifetime and response exceptions."""

from __future__ import annotations

import os
import queue
import select
import threading
import time

from .framing import ProtocolError, strict_json


class SkillResponseTimeout(ProtocolError):
    """The SKILL request was sent, but its execution result is not known yet."""

class SkillReplyWriteFailed(SkillResponseTimeout):
    """The private SKILL stage log confirms that the IPC reply write failed."""

class StdioLines:
    """Watch SKILL process lifetime even while the TCP broker is disconnected."""

    def __init__(self, source, control=None, *, decode=None, backpressure=False):
        self.source = source
        self.control = control
        self.decode = decode
        self.backpressure = backpressure
        self.closed = threading.Event()
        self.lines = queue.Queue(maxsize=8)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _publish(self, line):
        if self.decode is not None:
            if len(line) > 262_144 or not line.endswith(b"\n"):
                raise ProtocolError("SKILL line exceeds limit or is truncated")
            self._enqueue(self.decode(line))
        elif self.control and line in (b"@show\n", b"@shutdown\n"):
            self.control(line[1:-1].decode("ascii"))
        elif self.control and line.startswith((b"@submit ", b"@submit64 ")):
            if len(line) > 262_144:
                raise ValueError("Quick input envelope too large")
            if line.startswith(b"@submit64 "):
                from .quick_codec import decode_submission

                message = decode_submission(line[10:])
            else:
                message = strict_json(line[8:])
            self.control(message)
        elif self.control and line.startswith(b"@binding "):
            if len(line) > 262_144 or not line.endswith(b"\n"):
                raise ValueError("Native binding envelope too large or incomplete")
            self.control(strict_json(line[9:]))
        else:
            self._enqueue(line)

    def _enqueue(self, line):
        if not self.backpressure:
            self.lines.put(line, timeout=1)
            return
        # Desktop notices may wait behind a busy host. Keep this one line and
        # let pipe backpressure bound upstream writes, without retiring control.
        while not self.closed.is_set():
            try:
                self.lines.put(line, timeout=0.1)
                return
            except queue.Full:
                continue

    def close(self, timeout=1.5):
        self.closed.set()
        self.thread.join(timeout=timeout)

    def _read(self):
        try:
            # Do not hold BufferedReader's lock in a daemon thread at interpreter exit.
            fd = self.source.fileno()
            pending = bytearray()
            while not self.closed.is_set():
                if not select.select([fd], [], [], 0.1)[0]:
                    continue
                chunk = os.read(fd, 16_384)
                if not chunk:
                    if pending:
                        self._publish(bytes(pending))
                    return
                pending.extend(chunk)
                while b"\n" in pending:
                    end = pending.index(b"\n") + 1
                    line = bytes(pending[:end])
                    del pending[:end]
                    self._publish(line)
                    if len(line) > 262_144:
                        return
                if len(pending) > 262_144:
                    self._publish(bytes(pending))
                    return
        except (OSError, ValueError, queue.Full):
            return
        finally:
            self.closed.set()

    def next(self, timeout=30):
        deadline = time.monotonic() + timeout
        while True:
            try:
                line = self.lines.get(timeout=0.1)
                if self.decode is None and (len(line) > 262_144 or not line.endswith(b"\n")):
                    raise ProtocolError("SKILL line exceeds limit or is truncated")
                return line
            except queue.Empty:
                if self.closed.is_set():
                    raise EOFError("SKILL process closed its channel") from None
                if time.monotonic() >= deadline:
                    raise SkillResponseTimeout("SKILL response timed out") from None
