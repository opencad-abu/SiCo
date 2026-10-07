"""Drain a search process incrementally with bounds on time and pipe bytes."""

from __future__ import annotations

import os
import selectors
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

MAX_STREAM_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 4096


def run_search_process(
    command: Sequence[str],
    cwd: Path,
    timeout: int,
    consume: Callable[[bytes], str | None],
) -> tuple[int, str, str | None]:
    """Return exit status, bounded stderr, and the reason for an early stop.

    The consumer returns a stop reason once it has enough records. Neither
    stdout nor stderr is collected using unbounded communicate()/readline().
    """
    deadline = time.monotonic() + timeout
    errors = bytearray()
    received = 0
    reason = None
    with (
        subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": os.defpath, "LC_ALL": "C.UTF-8"},
        ) as process,
        selectors.DefaultSelector() as selector,
    ):
        try:
            for stream in (process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map() and reason is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reason = "timeout"
                    break
                for key, _events in selector.select(remaining):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    received += len(chunk)
                    if received > MAX_STREAM_BYTES:
                        reason = "stream_limit"
                        break
                    if key.fileobj is process.stderr:
                        errors.extend(chunk[: max(0, MAX_STDERR_BYTES - len(errors))])
                    else:
                        reason = consume(chunk)
                        if reason:
                            break
            if reason is None:
                try:
                    process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    reason = "timeout"
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
    return process.returncode, errors.decode("utf-8", errors="replace"), reason
