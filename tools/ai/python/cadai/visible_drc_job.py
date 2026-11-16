"""Mutable runtime record for one visible-DRC job."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Job:
    values: dict[str, Any]
    runner: Any
    cached: dict[str, Any] | None = None
    restored: bool = False


__all__ = ["Job"]
