"""Stage output validation and Calibre LVS result classification."""

from __future__ import annotations

import re
from pathlib import Path

from .config import RceConfig


IGNORED_LVS_MARKER = "lvs-ignored-mismatch"
_LVS_INCORRECT = re.compile(r"\bLVS\s+completed\.\s+INCORRECT\b", re.IGNORECASE)


def lvs_errors_ignored(cfg: RceConfig) -> bool:
    """Return whether an explicit TOML setting permits an LVS mismatch."""
    return cfg.flag("lvs", "ignore_error")


def is_completed_lvs_mismatch(text: str) -> bool:
    """Return true only for Calibre's explicit completed-but-incorrect result."""
    return _LVS_INCORRECT.search(text) is not None


def path_signature(path: Path) -> tuple[int, int, int] | None:
    """Describe a file or directory well enough to reject stale stage output."""
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_dir():
        return stat.st_mtime_ns, stat.st_size, 1

    latest_mtime = stat.st_mtime_ns
    total_size = 0
    entry_count = 0
    try:
        for entry in path.rglob("*"):
            entry_stat = entry.stat()
            latest_mtime = max(latest_mtime, entry_stat.st_mtime_ns)
            entry_count += 1
            if entry.is_file():
                total_size += entry_stat.st_size
    except OSError:
        return None
    if entry_count == 0:
        return None
    return latest_mtime, total_size, entry_count
