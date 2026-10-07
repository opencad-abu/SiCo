"""Bounded SSE frames with structured data conversion and preserved event ordering."""

from __future__ import annotations

import json

from .json_values import strict_json
from .responses_tools import CompatibilityError

MAX_EVENT = 8 * 1024 * 1024


def transform_stream(stream, convert, stopped=lambda: False):
    lines, size = [], 0
    while not stopped():
        line = stream.readline(MAX_EVENT + 1)
        if not line:
            if lines:
                raise CompatibilityError("Truncated upstream SSE frame")
            return
        size += len(line)
        if size > MAX_EVENT:
            raise CompatibilityError("Upstream SSE frame exceeds limit")
        if line in (b"\n", b"\r\n"):
            data = [
                entry[5:].lstrip(b" ").rstrip(b"\r\n")
                for entry in lines
                if entry.startswith(b"data:")
            ]
            if data:
                payload = b"\n".join(data)
                encoded = (
                    payload
                    if payload == b"[DONE]"
                    else json.dumps(convert(strict_json(payload)), ensure_ascii=False).encode()
                )
                emitted = False
                output = []
                for entry in lines:
                    if entry.startswith(b"data:"):
                        if not emitted:
                            output.append(b"data: " + encoded + b"\n")
                            emitted = True
                    else:
                        output.append(entry)
                yield b"".join(output) + b"\n"
            else:
                yield b"".join(lines) + b"\n"
            lines, size = [], 0
        else:
            lines.append(line)
