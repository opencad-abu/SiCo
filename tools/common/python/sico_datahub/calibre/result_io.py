"""Bounded text reads shared by Calibre result adapters."""

from __future__ import annotations

from pathlib import Path

from ..errors import HubError

MAX_RESULT_BYTES = 32 * 1024 * 1024


def read_result_file(path):
    path = Path(path).expanduser().absolute()
    if not path.is_file():
        raise HubError("read_failed", "Calibre result file is unavailable")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise HubError("read_failed", "Calibre result file cannot be read") from exc
    if size > MAX_RESULT_BYTES:
        raise HubError("result_too_large", "Calibre result file exceeds its limit")
    try:
        return path, path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HubError("read_failed", "Calibre result file cannot be read") from exc


__all__ = ["MAX_RESULT_BYTES", "read_result_file"]
