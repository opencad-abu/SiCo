"""Shared cache result types, schema constants, and cache roots."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Generic, Mapping, TypeVar

from sicostate import project_directory
from sicotemp import selected_state
_CACHE_SCHEMA_VERSION = 3
DEFAULT_MEMBERSHIP_TTL_SECONDS = 300.0
_MONITOR_CACHE_SCHEMA_VERSION = 1
DEFAULT_MONITOR_QUEUE_TTL_SECONDS = 30.0 * 60.0
DEFAULT_MONITOR_MEMBERSHIP_TTL_SECONDS = 7.0 * 24.0 * 60.0 * 60.0
DEFAULT_MONITOR_CAPACITY_TTL_SECONDS = 15.0 * 24.0 * 60.0 * 60.0

_T = TypeVar("_T")


@dataclass(frozen=True)
class CacheReadResult(Generic[_T]):
    """A cache value together with its absolute age and freshness state."""

    value: _T | None
    refreshed_at: float | None
    fresh: bool


def default_cache_root(
    environ: Mapping[str, str] | None = None,
    *,
    cwd: Path | None = None,
    user_id: int | None = None,
    create: bool = False,
) -> Path:
    state = selected_state(environ, cwd=cwd, create=create)
    user = os.geteuid() if user_id is None else user_id
    return project_directory(state.parent, "lsf/" + str(user), create=create)


def default_monitor_cache_root(
    environ: Mapping[str, str] | None = None,
    *,
    cwd: Path | None = None,
    user_id: int | None = None,
    create: bool = False,
) -> Path:
    """Compatibility facade: Monitor and session caches share one launch root."""
    return default_cache_root(environ, cwd=cwd, user_id=user_id, create=create)
