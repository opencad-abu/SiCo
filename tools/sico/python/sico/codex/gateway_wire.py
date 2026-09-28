"""Bounded ASCII frames for interactive LSF pipes, independent of terminal banners."""

import base64
import json
import os
import select
import time

PREFIX = b"SICO-GATEWAY/1 "
LIMIT = 16384
MAX_BODY = 16 * 1024 * 1024


def encode(kind, **fields):
    return (
        PREFIX
        + json.dumps(dict(kind=kind, **fields), separators=(",", ":")).encode()
        + b"\n"
    )


def chunk(data):
    return encode("data", body=base64.b64encode(data).decode("ascii"))


def decode_data(row):
    return base64.b64decode(row["body"], validate=True)


class Frames:
    def __init__(self, stream):
        self.stream = stream
        self.pending = bytearray()
        self.noise = 0

    def next(self, timeout, cancelled=lambda: False):
        deadline = time.monotonic() + timeout
        while True:
            if cancelled():
                raise OSError("LSF gateway request cancelled")
            if b"\n" in self.pending:
                line, _, rest = self.pending.partition(b"\n")
                self.pending = bytearray(rest)
                if len(line) > LIMIT:
                    raise ValueError("LSF gateway frame exceeds limit")
                if line.startswith(PREFIX):
                    row = json.loads(line[len(PREFIX) :])
                    if not isinstance(row, dict):
                        raise ValueError("Invalid gateway frame")
                    return row
                self.noise += len(line) + 1
                if self.noise > 65536:
                    raise ValueError("LSF gateway did not establish its protocol")
                continue
            if len(self.pending) > LIMIT:
                raise ValueError("LSF gateway frame exceeds limit")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("LSF gateway queue or upstream timeout")
            if select.select([self.stream], [], [], min(0.1, remaining))[0]:
                data = os.read(self.stream.fileno(), 8192)
                if not data:
                    raise EOFError("LSF gateway worker disconnected")
                self.pending.extend(data)


def send(stream, data):
    view = memoryview(data)
    while view:
        written = stream.write(view)
        if not written:
            raise OSError("LSF gateway pipe closed")
        view = view[written:]
    stream.flush()
