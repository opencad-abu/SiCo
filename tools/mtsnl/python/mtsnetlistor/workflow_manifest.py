"""Durable workflow diagnostic JSON and process identity markers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Optional
from .artifacts import atomic_write_text
from .serialize import json_value


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, value: object) -> None:
    atomic_write_text(path, json.dumps(json_value(value), ensure_ascii=True, indent=2, sort_keys=True) + "\n")


def _manifest_update(path: Optional[Path], manifest: Optional[dict[str, object]], **values: object) -> None:
    """Best-effort manifest update used from both success and failure paths.

    A diagnostic write must never mask the original workflow exception.  The
    run directory is private and already created before this helper is called.
    """

    if path is None or manifest is None:
        return
    manifest.update(values)
    try:
        _write_json(path, manifest)
    except OSError:
        pass


def _process_start_time(pid: int) -> str:
    """Best-effort Linux process start marker for owner identity diagnostics."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = stat.rsplit(")", 1)[-1].split()
        # /proc stat field 22 is index 19 after the closing comm field.
        return fields[19] if len(fields) > 19 else ""
    except (OSError, ValueError, IndexError):
        return ""
