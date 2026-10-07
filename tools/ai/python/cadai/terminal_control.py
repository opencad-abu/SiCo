"""Private controller-to-terminal lifecycle channel."""

from __future__ import annotations

import os
import subprocess
import threading

from .transport import VirtuosoTransport

TERMINAL_CONTROL_FD_ENV = "SICO_AI_CONTROL_FD"
TERMINAL_POLL_INTERVAL = 0.1
TERMINAL_SHUTDOWN_TIMEOUT = 5.0
TERMINAL_TERMINATE_TIMEOUT = 2.0


class TerminalControl:
    def __init__(self, read_fd: int, write_fd: int) -> None:
        self._read_fd = read_fd
        self._write_fd = write_fd
        self._lock = threading.Lock()

    @classmethod
    def create(cls) -> TerminalControl:
        read_fd, write_fd = os.pipe()
        return cls(read_fd, write_fd)

    @property
    def read_fd(self) -> int:
        if self._read_fd < 0:
            raise RuntimeError("terminal control read descriptor is closed")
        return self._read_fd

    def close_reader(self) -> None:
        with self._lock:
            if self._read_fd >= 0:
                os.close(self._read_fd)
                self._read_fd = -1

    def close_writer(self) -> None:
        with self._lock:
            self._close_writer_locked()

    def close(self) -> None:
        with self._lock:
            if self._read_fd >= 0:
                os.close(self._read_fd)
                self._read_fd = -1
            self._close_writer_locked()

    def send(self, command: str) -> bool:
        payload = command.encode("ascii") + b"\n"
        with self._lock:
            if self._write_fd < 0:
                return False
            try:
                offset = 0
                while offset < len(payload):
                    written = os.write(self._write_fd, payload[offset:])
                    if written <= 0:
                        raise OSError("terminal control pipe made no write progress")
                    offset += written
            except (BrokenPipeError, OSError):
                self._close_writer_locked()
                return False
        return True

    def _close_writer_locked(self) -> None:
        if self._write_fd >= 0:
            os.close(self._write_fd)
            self._write_fd = -1


def _drain_terminal_events(
    transport: VirtuosoTransport, control: TerminalControl
) -> None:
    while True:
        event = transport.next_event(timeout=0)
        if event is None:
            return
        if event == "terminal.show":
            control.send("show")


def _drain_live_events(transport: VirtuosoTransport, handler) -> None:
    """Dispatch bounded live events without coupling them to terminal control."""
    next_live_event = getattr(transport, "next_live_event", None)
    if not callable(next_live_event) or handler is None:
        return
    while True:
        event = next_live_event(timeout=0)
        if event is None:
            return
        try:
            handler(event)
        except Exception:
            # A malformed/late live event must not terminate the terminal
            # lifecycle. The session records its own bounded error state.
            continue


def wait_for_terminal(
    process: subprocess.Popen[bytes],
    transport: VirtuosoTransport,
    control: TerminalControl,
    stop_requested: threading.Event,
    live_event_handler=None,
) -> tuple[int | None, bool]:
    while True:
        return_code = process.poll()
        if return_code is not None:
            return return_code, False
        _drain_terminal_events(transport, control)
        _drain_live_events(transport, live_event_handler)
        if transport.is_closed or stop_requested.is_set():
            return None, True
        stop_requested.wait(TERMINAL_POLL_INTERVAL)


def shutdown_terminal(
    process: subprocess.Popen[bytes], control: TerminalControl
) -> int:
    return_code = process.poll()
    if return_code is not None:
        control.close_writer()
        return return_code

    control.send("shutdown")
    control.close_writer()
    try:
        return process.wait(timeout=TERMINAL_SHUTDOWN_TIMEOUT)
    except subprocess.TimeoutExpired:
        pass

    try:
        process.terminate()
    except OSError:
        pass
    try:
        return process.wait(timeout=TERMINAL_TERMINATE_TIMEOUT)
    except subprocess.TimeoutExpired:
        pass

    try:
        process.kill()
    except OSError:
        pass
    try:
        return process.wait(timeout=TERMINAL_TERMINATE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return process.poll() if process.poll() is not None else 1


__all__ = [
    "TERMINAL_CONTROL_FD_ENV",
    "TERMINAL_SHUTDOWN_TIMEOUT",
    "TERMINAL_TERMINATE_TIMEOUT",
    "TerminalControl",
    "shutdown_terminal",
    "wait_for_terminal",
]
