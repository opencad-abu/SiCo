"""Isolated process-group execution used by qualification pilots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ProcessResult:
    command: tuple[str, ...]
    cwd: str
    started_at: str
    finished_at: str
    elapsed_seconds: float
    returncode: int | None
    timed_out: bool
    log_file: str

    def to_dict(self) -> dict[str, object]:
        return {
            "command": list(self.command),
            "cwd": self.cwd,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": self.elapsed_seconds,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "log_file": self.log_file,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _terminate_group(process: subprocess.Popen[bytes], grace: float = 5.0) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait()


def run_process_group(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    log_file: Path,
    timeout: float,
) -> ProcessResult:
    argv = tuple(str(item) for item in command)
    started_at = _now()
    started = time.monotonic()
    timed_out = False
    returncode: int | None = None
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("wb") as stream:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_group(process)
            returncode = process.returncode
    return ProcessResult(
        command=argv,
        cwd=str(cwd),
        started_at=started_at,
        finished_at=_now(),
        elapsed_seconds=time.monotonic() - started,
        returncode=returncode,
        timed_out=timed_out,
        log_file=str(log_file),
    )
