"""Launch one explicitly configured Calibre flow process with bounded logs."""

from __future__ import annotations

import os
import re
import signal
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from ..errors import HubError

_MAX_TIMEOUT = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class ProcessOutcome:
    pid: int
    exit_code: int
    timed_out: bool
    started_at: str
    finished_at: str


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stop(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        try:
            process.terminate()
        except OSError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def stop_process(process):
    """Terminate a process group owned by the Calibre runner."""
    if process is not None and process.poll() is None:
        _stop(process)


def run_process(
    command: Sequence[str],
    *,
    cwd,
    env: Mapping[str, str],
    stdout_path,
    stderr_path,
    timeout,
    on_start=None,
) -> ProcessOutcome:
    if (
        not isinstance(command, (list, tuple))
        or not command
        or any(not isinstance(value, str) or not value for value in command)
    ):
        raise HubError("invalid_arguments", "Invalid Calibre command")
    if type(timeout) not in {int, float} or not 0 < timeout <= _MAX_TIMEOUT:
        raise HubError("invalid_arguments", "Invalid Calibre timeout")
    stdout_path = Path(stdout_path)
    stderr_path = Path(stderr_path)
    try:
        stdout_fd = os.open(
            str(stdout_path),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
        )
        stderr_fd = os.open(
            str(stderr_path),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
        )
    except OSError as exc:
        for descriptor in locals().get("stdout_fd", None), locals().get("stderr_fd", None):
            if descriptor is not None:
                os.close(descriptor)
        raise HubError("read_failed", "Calibre log files cannot be created") from exc
    started_at = _now()
    try:
        try:
            process = subprocess.Popen(
                list(command),
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=stdout_fd,
                stderr=stderr_fd,
                env=dict(env),
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            raise HubError("capability_not_available", "Calibre flow could not start") from exc
        finally:
            os.close(stdout_fd)
            os.close(stderr_fd)
            stdout_fd = stderr_fd = None
        if on_start is not None:
            on_start(process)
        timed_out = False
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _stop(process)
            exit_code = process.poll()
            if exit_code is None:
                exit_code = -signal.SIGKILL
        return ProcessOutcome(process.pid, exit_code, timed_out, started_at, _now())
    finally:
        if stdout_fd is not None:
            os.close(stdout_fd)
        if stderr_fd is not None:
            os.close(stderr_fd)


def probe_version(executable, *, timeout=5.0) -> str:
    """Return a bounded Calibre version line without treating failure as fatal."""
    executable = Path(executable)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return ""
    try:
        completed = subprocess.run(
            [str(executable), "-version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    version = next(
        (
            line for line in lines
            if re.search(r"\bcalibre\b", line, re.IGNORECASE)
            and re.search(r"\d", line)
        ),
        "",
    )
    version = re.sub(r"^\s*//\s*", "", version)
    return version[:128]


__all__ = ["ProcessOutcome", "probe_version", "run_process", "stop_process"]
