"""First-sample host capacities retained for a collector session."""

from __future__ import annotations

from threading import Lock
from typing import Callable, Mapping

from .command_runner import CollectorError


class HostCapacityCache:
    """Share one immutable lshosts capacity sample across monitor refreshes."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._capacities: dict[str, int | None] | None = None

    def load(self) -> dict[str, int | None] | None:
        with self._lock:
            if self._capacities is None:
                return None
            return dict(self._capacities)

    def store(
        self, capacities: Mapping[str, int | None]
    ) -> dict[str, int | None]:
        normalized = dict(capacities)
        with self._lock:
            if self._capacities is None:
                self._capacities = normalized
            return dict(self._capacities)


class CapacityReader:
    def __init__(self, collect: Callable[[], dict[str, int | None]], cache: HostCapacityCache):
        self._collect = collect
        self._cache = cache

    def collect(self) -> dict[str, int | None]:
        cached = self._cache.load()
        if cached is not None:
            return cached
        try:
            capacities = self._collect()
        except CollectorError:
            self._cache.store({})
            raise
        return self._cache.store(capacities)
