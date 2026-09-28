"""TTL-governed LSF monitor topology refresh; independent of session caches."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Sequence

from .cache import MonitorTopologyCache
from .command_runner import CollectorCancelled, CollectorError
from .model import Diagnostic, QueueInfo

_MEMBERSHIP_WORKERS = 4


class MonitorTopology:
    def __init__(self, *, queues: Callable, host_queues: Callable, capacities: Callable):
        self._queues = queues
        self._host_queues = host_queues
        self._capacities = capacities

    def _memberships(
        self, hosts: Sequence[str]
    ) -> tuple[dict[str, tuple[str, ...]], tuple[Diagnostic, ...]]:
        """Collect a complete live membership map without session-cache writes."""
        normalized = tuple(dict.fromkeys(hosts))
        if not normalized:
            return {}, ()
        verified: dict[str, tuple[str, ...]] = {}
        failures: list[str] = []
        executor = ThreadPoolExecutor(
            max_workers=min(_MEMBERSHIP_WORKERS, len(normalized)),
            thread_name_prefix="cad-lsf-monitor-membership",
        )
        futures = {
            executor.submit(self._host_queues, host): host
            for host in normalized
        }
        try:
            for future in as_completed(futures):
                host = futures[future]
                try:
                    verified[host] = future.result()
                except CollectorCancelled:
                    for pending in futures:
                        pending.cancel()
                    raise
                except CollectorError as exc:
                    failures.append(str(exc))
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        if not failures:
            return verified, ()
        message = f"Failed to verify queue membership for {len(failures)} host(s)"
        if failures[0]:
            message += f"; first failure: {failures[0]}"
        return verified, (Diagnostic("queue_membership_failed", message),)

    def _refresh(
        self,
        cache: MonitorTopologyCache,
        hosts: Sequence[str],
        *,
        refresh_queues: bool,
        refresh_memberships: bool,
        refresh_capacities: bool,
    ) -> tuple[
        tuple[QueueInfo, ...] | None,
        dict[str, tuple[str, ...]] | None,
        dict[str, int | None] | None,
        tuple[Diagnostic, ...],
    ]:
        """Refresh requested cache sections concurrently where possible."""
        diagnostics: list[Diagnostic] = []
        queues: tuple[QueueInfo, ...] | None = None
        memberships: dict[str, tuple[str, ...]] | None = None
        capacities: dict[str, int | None] | None = None
        tasks = int(refresh_queues) + int(refresh_memberships) + int(refresh_capacities)
        if tasks == 0:
            return queues, memberships, capacities, ()
        executor = ThreadPoolExecutor(
            max_workers=min(3, tasks), thread_name_prefix="cad-lsf-topology"
        )
        queue_future = executor.submit(self._queues) if refresh_queues else None
        membership_future = (
            executor.submit(self._memberships, tuple(hosts))
            if refresh_memberships
            else None
        )
        capacity_future = (
            executor.submit(self._capacities)
            if refresh_capacities
            else None
        )
        try:
            if queue_future is not None:
                try:
                    queues = queue_future.result()
                    if not cache.store_queues(queues):
                        diagnostics.append(Diagnostic(
                            "monitor_cache_write_failed",
                            "Cannot cache the refreshed LSF queue sample",
                        ))
                except CollectorError as exc:
                    diagnostics.append(Diagnostic("queue_collection_failed", str(exc)))
            if membership_future is not None:
                try:
                    memberships, membership_diagnostics = membership_future.result()
                    diagnostics.extend(membership_diagnostics)
                    # Only publish a complete topology. Partial membership results
                    # remain stale so a later refresh retries the failed hosts.
                    if len(memberships) == len(tuple(dict.fromkeys(hosts))):
                        if not cache.store_host_memberships(memberships):
                            diagnostics.append(Diagnostic(
                                "monitor_cache_write_failed",
                                "Cannot cache refreshed LSF host membership",
                            ))
                    else:
                        memberships = None
                except CollectorError as exc:
                    diagnostics.append(Diagnostic("queue_membership_failed", str(exc)))
            if capacity_future is not None:
                try:
                    capacities = capacity_future.result()
                    if not cache.store_host_capacities(capacities):
                        diagnostics.append(Diagnostic(
                            "monitor_cache_write_failed",
                            "Cannot cache refreshed LSF host capacities",
                        ))
                except CollectorError as exc:
                    diagnostics.append(Diagnostic("capacity_collection_failed", str(exc)))
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        return queues, memberships, capacities, tuple(diagnostics)

    def refresh(
        self,
        cache: MonitorTopologyCache,
        host_names: Sequence[str],
        *,
        force_topology: bool = False,
    ) -> tuple[Diagnostic, ...]:
        """Refresh expired Monitor topology after a dynamic sample is visible."""
        normalized_hosts = tuple(dict.fromkeys(host_names))
        queues = cache.load_queues()
        memberships = cache.load_host_memberships()
        capacities = cache.load_host_capacities()
        known_memberships = memberships.value or {}
        refresh_queues = force_topology or not queues.fresh
        refresh_memberships = force_topology or not memberships.fresh or any(
            host not in known_memberships for host in normalized_hosts
        )
        refresh_capacities = force_topology or not capacities.fresh or any(
            host not in (capacities.value or {}) for host in normalized_hosts
        )
        return self._refresh(
            cache,
            normalized_hosts,
            refresh_queues=refresh_queues,
            refresh_memberships=refresh_memberships,
            refresh_capacities=refresh_capacities,
        )[3]
