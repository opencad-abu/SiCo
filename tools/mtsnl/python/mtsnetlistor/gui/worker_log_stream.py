"""Marshal and bound worker output before rendering it on the GUI thread."""

from __future__ import annotations

from queue import Empty, SimpleQueue
from .controller import CATALOG_LOG_PREFIX
from .worker_log import clean_worker_log_line, safe_catalog_log, useful_worker_log


class WorkerLogStream:
    def __init__(self, append) -> None:
        self._append = append
        self._queue = SimpleQueue()
        self._tails = {"OCEAN": "", "dbAccess": ""}

    def receive(self, message: str) -> None:
        """Queue background-worker output for consumption on the Qt thread."""

        if message:
            self._queue.put(str(message))

    def drain(self, *, flush_tail: bool = False) -> None:
        """Append selected worker diagnostics without touching Qt off-thread."""

        while True:
            try:
                raw = self._queue.get_nowait()
            except Empty:
                break
            channel = "OCEAN"
            message = raw
            if raw.startswith(CATALOG_LOG_PREFIX):
                channel = "dbAccess"
                message = raw[len(CATALOG_LOG_PREFIX):]
            # Cadence can occasionally emit a very long line without a newline
            # while loading a third-party interface. Keep the pending fragment
            # bounded so an unbounded producer cannot grow GUI memory between
            # timer ticks; completed lines are still handled normally.
            self._tails[channel] = (
                self._tails[channel] + message
            )[-65536:]

        for channel, tail in tuple(self._tails.items()):
            if not tail:
                continue
            lines = tail.splitlines(keepends=True)
            if flush_tail or (lines and lines[-1].endswith(("\n", "\r"))):
                self._tails[channel] = ""
            else:
                self._tails[channel] = lines.pop() if lines else tail
            for line in lines:
                show = (
                    safe_catalog_log(line)
                    if channel == "dbAccess"
                    else useful_worker_log(line)
                )
                if show:
                    cleaned = clean_worker_log_line(line.rstrip("\r\n"))
                    self._append(f"{channel}: {cleaned}")
