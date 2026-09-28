"""Private records for one completed or in-flight source catalog query."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
from typing import Callable
from .catalog_result import CatalogResult


@dataclass(frozen=True)
class _SourceCacheEntry:
    result: CatalogResult
    created_at: float
    provider_seconds: float


@dataclass
class _SourceCacheFlight:
    done: Event
    # True when this provider was created specifically to satisfy a forced
    # refresh.  Later force-refresh callers may join it without scheduling yet
    # another generation.
    refresh_generation: bool = False
    result: CatalogResult | None = None
    error: BaseException | None = None
    # ``error`` alone is not sufficient to tell a waiter whether it should
    # retry: the owner may have been canceled while Cadence was starting, and
    # the provider can wrap that cancellation in a generic CatalogError.
    canceled: bool = False
    provider_seconds: float = 0.0
    # A force-refresh request can arrive while this provider is running.  Let
    # the current flight finish so two Cadence providers never run at once,
    # but make every waiter retry against a successor flight.
    refresh_requested: bool = False
    # Concurrent process tabs can share one source provider. Broadcast its
    # progress to every current waiter; their controller token gates discard
    # output after a tab is canceled or superseded.
    output_callbacks: list[Callable[[str], object]] = field(default_factory=list)
