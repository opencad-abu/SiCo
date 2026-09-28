"""Serialize source catalog providers and own all process-local cache state."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from threading import Event, RLock
import time
from typing import Callable, ContextManager

from .catalog_cache_state import _SourceCacheEntry, _SourceCacheFlight
from .catalog_result import CatalogResult, _cache_diagnostics, _cached_source_result
from .errors import CatalogError

Provider = Callable[[Callable[[str], object]], ContextManager[tuple[CatalogResult, float]]]


class _SourceCatalogCache:
    def __init__(self) -> None:
        self._lock = RLock()
        self._entries: dict[str, _SourceCacheEntry] = {}
        self._flights: dict[str, _SourceCacheFlight] = {}
        self._epoch = 0

    def clear(self, cds_library_file: str | Path | None = None) -> int:
        selected = None if cds_library_file is None else Path(cds_library_file).expanduser().resolve()
        removed = 0
        with self._lock:
            self._epoch += 1
            for key, entry in tuple(self._entries.items()):
                if selected is not None and entry.result.catalog.cds_library_file != selected:
                    continue
                self._entries.pop(key, None)
                removed += 1
        return removed

    def info(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._entries), "inflight": len(self._flights)}

    def _select(self, key, ttl, refresh, callback):
        """Select a completed entry or register/join a flight under one lock."""
        with self._lock:
            flight = self._flights.get(key)
            if not refresh:
                entry = self._entries.get(key)
                if entry is not None:
                    age = time.monotonic() - entry.created_at
                    if ttl <= 0 or age > ttl:
                        self._entries.pop(key, None)
                    else:
                        return entry, age, None, False, 0
            elif flight is None or not flight.refresh_generation:
                # Joining an active refresh during cleanup preserves its entry.
                self._entries.pop(key, None)
            if flight is None:
                flight = _SourceCacheFlight(
                    done=Event(), refresh_generation=refresh,
                    output_callbacks=[] if callback is None else [callback],
                )
                self._flights[key] = flight
                return None, None, flight, True, self._epoch
            if refresh and not flight.refresh_generation:
                flight.refresh_requested = True
            elif callback is not None:
                flight.output_callbacks.append(callback)
            return None, None, flight, False, 0

    def _wait(self, flight, timeout, cancel_event):
        wait_started = time.monotonic()
        while not flight.done.wait(0.05):
            if cancel_event is not None and cancel_event.is_set():
                raise CatalogError("source catalog request canceled while waiting for a shared request")
            if time.monotonic() - wait_started >= max(0.1, float(timeout)):
                raise CatalogError("timed out waiting for shared source catalog request")
        if cancel_event is not None and cancel_event.is_set():
            raise CatalogError("source catalog request canceled while waiting for a shared request")

    def _emit(self, flight, message):
        with self._lock:
            callbacks = tuple(flight.output_callbacks)
        for callback in callbacks:
            try:
                callback(message)
            except BaseException:
                # Advisory output must not affect any subscriber or provider.
                continue

    def _store(self, key, flight, epoch, result, seconds, cacheable):
        if not cacheable or not result.authoritative:
            return False
        with self._lock:
            if epoch != self._epoch or self._flights.get(key) is not flight or flight.refresh_requested:
                return False
            self._entries[key] = _SourceCacheEntry(result, time.monotonic(), seconds)
            return True

    def _finish(self, key, flight):
        # Signaling and removal use the submitter lock: no duplicate provider
        # can enter between removal and completion notification.
        with self._lock:
            current = self._flights.get(key)
            if current is flight:
                flight.done.set()
                self._flights.pop(key, None)
        if current is not flight:
            flight.done.set()

    def _produce(self, key, flight, epoch, provider, cancel_event, cacheable, decorate):
        completed = False
        try:
            with provider(partial(self._emit, flight)) as (result, seconds):
                stored = self._store(key, flight, epoch, result, seconds, cacheable)
                decorated = decorate(result, "miss-stored" if stored else "miss-uncached", seconds)
                flight.result = result
                flight.provider_seconds = seconds
                completed = True
                return decorated
        except BaseException as exc:
            flight.error = exc
            # A cleanup error is shared as-is, even if cancellation arrives
            # during cleanup. Only a canceled provider permits a waiter retry.
            flight.canceled = not completed and cancel_event is not None and cancel_event.is_set()
            if completed:
                with self._lock:
                    self._entries.pop(key, None)
            raise
        finally:
            self._finish(key, flight)

    def load(
        self, key: str, *, ttl: float, force_refresh: bool, timeout: float,
        cancel_event: Event | None, output_callback: Callable[[str], object] | None,
        fingerprint_complete: bool, fingerprint_ms: float, request_started: float,
        provider: Provider, retry: Callable[[bool], CatalogResult], shared_cancel_retry: bool,
    ) -> CatalogResult:
        def decorate(result, status, seconds, age=None):
            diagnostics = _cache_diagnostics(
                status=status, fingerprint_ms=fingerprint_ms,
                provider_ms=seconds * 1000.0,
                total_ms=(time.perf_counter() - request_started) * 1000.0,
                age_ms=None if age is None else age * 1000.0,
            )
            return _cached_source_result(result, diagnostics=(*result.diagnostics, *diagnostics))

        entry, age, flight, owner, epoch = self._select(key, ttl, force_refresh, output_callback)
        if entry is not None:
            return decorate(entry.result, "hit", entry.provider_seconds, age)
        if not owner:
            self._wait(flight, timeout, cancel_event)
            if flight.refresh_requested:
                return retry(shared_cancel_retry)
            if flight.canceled and shared_cancel_retry:
                return retry(False)
            if flight.error is not None:
                raise flight.error
            if flight.result is None:
                raise CatalogError("shared source catalog request completed without a result")
            return decorate(flight.result, "shared-wait", flight.provider_seconds)
        return self._produce(
            key, flight, epoch, provider, cancel_event,
            fingerprint_complete and ttl > 0, decorate,
        )


_CACHE = _SourceCatalogCache()


def clear_source_catalog_cache(cds_library_file: str | Path | None = None) -> int:
    """Drop completed entries; prevent active requests from repopulating them."""
    return _CACHE.clear(cds_library_file)


def source_catalog_cache_info() -> dict[str, int]:
    """Return non-sensitive counts without exposing cache/flight objects."""
    return _CACHE.info()


def resolve_source_catalog(key: str, **options) -> CatalogResult:
    """Resolve one source request through the sole process-local cache."""
    return _CACHE.load(key, **options)
