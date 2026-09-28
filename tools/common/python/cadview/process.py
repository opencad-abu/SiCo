"""Run a Cadence importer while owning its complete process group."""

from __future__ import annotations

import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from cadenv import cadence_mps_environment_names
from .process_group import terminate_process_group as _terminate_process_group


class _ImportInterrupted(BaseException):
    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def run_cadence_import(
    command: List[str],
    environment: Mapping[str, str],
    temporary: Path,
    *,
    timeout: Optional[float] = None,
    cancel: Optional[object] = None,
) -> subprocess.CompletedProcess[str]:
    """Run one import and forward termination to all of its descendants.

    ``cdsTextTo5x`` is invoked from both the command-line entry point and the
    GUI controller's worker pool.  Python only permits installing signal
    handlers in the main thread, so the worker-thread path uses short
    ``communicate(timeout=...)`` polls for cancellation/timeout instead of
    attempting ``signal.signal``.  The command-line path retains signal
    forwarding so a SIGTERM/SIGINT received by the wrapper also reaches the
    complete Cadence process group.
    """

    if timeout is not None and timeout <= 0:
        raise ValueError("import timeout must be greater than zero")
    mps_selectors = cadence_mps_environment_names(environment)
    if mps_selectors:
        raise ValueError(
            "refusing Cadence import with inherited MPS selector(s): "
            + ", ".join(mps_selectors)
        )

    def cancellation_requested() -> bool:
        return bool(
            cancel is not None
            and getattr(cancel, "is_set", lambda: False)()
        )

    def completed_after_termination(signum: int) -> subprocess.CompletedProcess[str]:
        _terminate_process_group(process, signum)
        stdout, stderr = process.communicate()
        return subprocess.CompletedProcess(
            command, 128 + signum, stdout, stderr
        )

    def run_with_polling() -> subprocess.CompletedProcess[str]:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            # A bounded communicate timeout lets a GUI cancellation event be
            # observed without blocking the worker indefinitely.  The child
            # process still owns its stdout/stderr pipes, so communicate is
            # used rather than wait to avoid a pipe-fill deadlock.
            interval = 0.1
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return completed_after_termination(signal.SIGTERM)
                interval = min(interval, remaining)
            try:
                stdout, stderr = process.communicate(timeout=interval)
                return subprocess.CompletedProcess(
                    command, process.returncode, stdout, stderr
                )
            except subprocess.TimeoutExpired:
                if process.poll() is not None:
                    # The leader exited, but a helper still holds a pipe.
                    # Complete teardown without turning its exit into a timeout.
                    _terminate_process_group(process)
                    stdout, stderr = process.communicate()
                    return subprocess.CompletedProcess(
                        command, process.returncode, stdout, stderr
                    )
                if cancellation_requested():
                    return completed_after_termination(signal.SIGTERM)
                if deadline is not None and time.monotonic() >= deadline:
                    return completed_after_termination(signal.SIGTERM)

    previous_handlers: Dict[int, object] = {}

    def interrupt(signum: int, _frame: object) -> None:
        raise _ImportInterrupted(signum)

    # ``signal.signal`` raises ValueError outside the main interpreter
    # thread.  The GUI deliberately runs publication in a worker thread, so
    # only install process-level handlers where Python permits it.
    install_handlers = threading.current_thread() is threading.main_thread()
    handled_signals = (signal.SIGTERM, signal.SIGINT)
    if install_handlers:
        for signum in handled_signals:
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupt)
    try:
        process = subprocess.Popen(
            command,
            cwd=temporary,
            env=dict(environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        result = run_with_polling()
    except _ImportInterrupted as exc:
        if "process" not in locals():
            return subprocess.CompletedProcess(command, 128 + exc.signum, "", "")
        _terminate_process_group(process, exc.signum)
        stdout, stderr = process.communicate()
        result = subprocess.CompletedProcess(
            command, 128 + exc.signum, stdout, stderr
        )
    finally:
        if install_handlers:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
        if "process" in locals():
            _terminate_process_group(process)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
    return result
