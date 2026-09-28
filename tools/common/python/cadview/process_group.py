"""Bounded teardown of a worker's private POSIX process group."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import time


def _group_running(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    # Linux can retain orphan zombies until its init/subreaper reaps them.
    # They hold neither OA handles nor pipes and must not delay every retry.
    proc = Path("/proc")
    if not (proc / "self" / "stat").is_file():
        return True
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and fields[0] not in {"Z", "X"}:
                return True
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, ValueError, IndexError):
            # If procfs cannot establish membership, use the conservative
            # killpg result rather than declaring teardown complete.
            return True
    return False


def _signal_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        pass


def _wait_group(process: subprocess.Popen[str], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        process.poll()
        if not _group_running(process.pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def terminate_process_group(
    process: subprocess.Popen[str], signum: int = signal.SIGTERM, timeout: float = 3.0
) -> None:
    """Finish a group created with ``start_new_session=True`` before returning.

    The leader may already have exited. Escalation depends on the entire
    group, so a TERM-resistant child cannot outlive a fast-exiting parent.
    This owns only the worker's group, never the host Virtuoso or other jobs.
    """

    pgid = process.pid
    if pgid == os.getpgrp():
        raise ValueError("refusing to terminate the caller's process group")
    try:
        if os.getpgid(pgid) != pgid:
            raise ValueError("worker does not own a private process group")
    except ProcessLookupError:
        pass
    process.poll()
    if _group_running(pgid):
        _signal_group(pgid, signum)
        if not _wait_group(process, timeout):
            _signal_group(pgid, signal.SIGKILL)
            if not _wait_group(process, timeout):
                raise RuntimeError(f"worker process group {pgid} did not terminate")
    process.wait(timeout=timeout)
