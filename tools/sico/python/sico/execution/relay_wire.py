"""Bounded byte framing for the private managed Bash relay channel."""

import os
import select
import struct
import time

from ..transport import framing

MAX_FRAME = 512 * 1024


def encode(value):
    return framing.encode(value, MAX_FRAME)


def decoder():
    return framing.FrameDecoder(MAX_FRAME)


def receive(fd, timeout=None):
    deadline = time.monotonic() + timeout if timeout is not None else None
    frames = decoder()
    while True:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                raise TimeoutError('Relay input deadline expired')
        # Read exactly one frame, leaving later permits/commands in the pipe.
        needed = 4 - len(frames.buffer) if len(frames.buffer) < 4 else (
            struct.unpack('!I', frames.buffer[:4])[0] + 4 - len(frames.buffer))
        data = os.read(fd, min(65536, needed))
        if not data:
            raise EOFError('Relay input closed')
        rows = frames.feed(data)
        if rows:
            return rows[0]


def send(fd, value):
    data = memoryview(encode(value))
    while data:
        count = os.write(fd, data)
        if count <= 0:
            raise BrokenPipeError('Relay output closed')
        data = data[count:]
