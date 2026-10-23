"""Translate owning-process termination signals into a pollable stop event."""

from __future__ import annotations

import signal
import threading

SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


class TerminationSignal:
    """Own the signal handlers while callers keep their normal shutdown path."""

    def __init__(self):
        self.received = threading.Event()
        self._previous = {}

    def install(self):
        if self._previous:
            return self
        for number in SIGNALS:
            self._previous[number] = signal.signal(number, self._receive)
        return self

    def restore(self):
        for number, previous in self._previous.items():
            signal.signal(number, previous)
        self._previous.clear()

    def _receive(self, _number, _frame):
        self.received.set()

    def __enter__(self):
        return self.install()

    def __exit__(self, *_exc):
        self.restore()
