"""Drain owned workers before Qt or an IPC termination exits the GUI."""

from __future__ import annotations

from contextlib import contextmanager
import signal
from threading import Event
from typing import Iterator


@contextmanager
def managed_gui_shutdown(application: object, window: object) -> Iterator[None]:
    from PyQt5.QtCore import QTimer

    requested = Event()
    closed = False

    def close() -> None:
        nonlocal closed
        if not closed:
            closed = True
            window.close()

    def request_close(_signum: int, _frame: object) -> None:
        # Signal handlers must not acquire controller locks or run Qt slots
        # reentrantly. The timer dispatches shutdown on the Qt event loop.
        requested.set()

    def poll() -> None:
        if requested.is_set():
            close()

    previous = {}
    timer = QTimer(application)
    timer.setInterval(100)
    timer.timeout.connect(poll)
    application.aboutToQuit.connect(close)
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, request_close)
        timer.start()
        yield
    finally:
        timer.stop()
        try:
            close()
        finally:
            application.aboutToQuit.disconnect(close)
            for signum, handler in previous.items():
                signal.signal(signum, handler)
