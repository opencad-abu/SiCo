"""Bind the selector process to the owning Virtuoso process identity."""

from __future__ import annotations

import os
from pathlib import Path
from typing import MutableMapping
from cadgui.lifecycle import capture_parent_identity as _capture_parent_identity


_PARENT_PID_ENV = "DRC_RULE_SELECT_PARENT_PID"


def capture_parent_identity(
    parent_pid: int | str | None = None,
    environ: MutableMapping[str, str] | None = None,
    *,
    proc_root: Path = Path("/proc"),
) -> tuple[int, str] | None:
    """Capture the owning Virtuoso PID and Linux start time for reuse checks."""
    return _capture_parent_identity(
        parent_pid,
        environ,
        parent_env=_PARENT_PID_ENV,
        proc_root=proc_root,
        current_pid=os.getpid(),
        current_parent_pid=os.getppid(),
    )
