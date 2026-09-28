"""Lifecycle helpers for GUI processes owned by a Virtuoso session."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Callable, MutableMapping, Tuple


ParentIdentity = Tuple[int, str]
_MAX_LINUX_PID = 2_147_483_647


def _read_process_identity(
    process_id: int, proc_root: Path = Path("/proc")
) -> ParentIdentity | None:
    try:
        stat = (proc_root / str(process_id) / "stat").read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return None
    _, separator, fields_text = stat.rpartition(") ")
    fields = fields_text.split() if separator else []
    if len(fields) <= 19:
        return None
    parent_pid, start_time = fields[1], fields[19]
    if not all(
        value.isascii() and value.isdigit() for value in (parent_pid, start_time)
    ):
        return None
    return int(parent_pid), start_time


def _process_has_ancestor(
    process_id: int, ancestor_pid: int, proc_root: Path = Path("/proc")
) -> bool:
    current = process_id
    visited: set[int] = set()
    while current > 1 and current not in visited:
        if current == ancestor_pid:
            return True
        visited.add(current)
        identity = _read_process_identity(current, proc_root)
        if identity is None:
            return False
        current = identity[0]
    return current == ancestor_pid


def capture_parent_identity(
    parent_pid: int | str | None = None,
    environ: MutableMapping[str, str] | None = None,
    *,
    parent_env: str | None = None,
    proc_root: Path = Path("/proc"),
    current_pid: int | None = None,
    current_parent_pid: int | None = None,
) -> ParentIdentity | None:
    """Validate and capture an owning process PID plus Linux start time."""
    target = os.environ if environ is None else environ
    raw_value: int | str | None = parent_pid
    if raw_value is None and parent_env:
        raw_value = target.get(parent_env)
    if raw_value is None:
        return None

    option = parent_env or "parent PID"
    if not sys.platform.startswith("linux"):
        raise RuntimeError(f"{option} parent monitoring requires Linux")
    raw = str(raw_value)
    if not raw or len(raw) > 10 or not raw.isascii() or not raw.isdigit():
        raise RuntimeError(f"{option} must be a positive decimal PID")
    process_id = int(raw)
    if not 1 < process_id <= _MAX_LINUX_PID:
        raise RuntimeError(f"{option} is outside the valid PID range")
    own_pid = os.getpid() if current_pid is None else current_pid
    if process_id == own_pid:
        raise RuntimeError(f"{option} must identify another process")
    child_pid = os.getppid() if current_parent_pid is None else current_parent_pid
    if not _process_has_ancestor(child_pid, process_id, proc_root):
        raise RuntimeError(f"{option} must identify an ancestor process")
    identity = _read_process_identity(process_id, proc_root)
    if identity is None:
        raise RuntimeError("the owning Virtuoso process exited before GUI startup")
    return process_id, identity[1]


def install_parent_monitor(
    owner,
    identity: ParentIdentity | None,
    on_exit: Callable[[], object],
    *,
    proc_root: Path = Path("/proc"),
    interval_ms: int = 500,
):
    """Close a GUI when its owning process exits or its PID is reused."""
    if identity is None:
        return None
    from PyQt5.QtCore import QTimer

    process_id, start_time = identity
    timer = QTimer(owner)
    timer.setInterval(interval_ms)

    def poll_parent() -> None:
        current = _read_process_identity(process_id, proc_root)
        if current is None or current[1] != start_time:
            timer.stop()
            on_exit()

    timer.timeout.connect(poll_parent)
    timer.start()
    poll_parent()
    return timer


def finalize_gui_exit(
    status: int,
    identity: ParentIdentity | None,
    hard_exit: Callable[[int], object] | None = None,
) -> int:
    """Use a hard exit for owned GUIs to avoid a blocked Qt shutdown."""
    if identity is not None:
        (hard_exit or os._exit)(status)
    return status
