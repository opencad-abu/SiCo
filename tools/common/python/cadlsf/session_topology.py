"""Permission topology retained for one LSF selection session."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Callable, Sequence

from .cache import SessionTopologyCache
from .command_runner import CollectorCancelled, CollectorError
from .model import Diagnostic, QueueInfo

_MEMBERSHIP_WORKERS = 4


class SessionTopology:
    def __init__(self, collect_queues: Callable, host_queues: Callable,
                 cache: SessionTopologyCache | None = None):
        self._collect_queues = collect_queues
        self._host_queues = host_queues
        self._cache = cache

    def queues(
        self,
    ) -> tuple[tuple[QueueInfo, ...], tuple[Diagnostic, ...]]:
        queues = self._collect_queues()
        diagnostics: list[Diagnostic] = []
        cached_names = (
            self._cache.load_queue_names()
            if self._cache is not None
            else None
        )
        if cached_names is None:
            if self._cache is not None:
                if not self._cache.store_queue_names(
                    [queue.name for queue in queues]
                ):
                    diagnostics.append(
                        Diagnostic(
                            "topology_cache_write_failed",
                            "Cannot cache accessible LSF queues; they will be "
                            "queried again on the next refresh",
                        )
                    )
        return (
            tuple(queue for queue in queues if queue.is_open),
            tuple(diagnostics),
        )

    def open_queues(self) -> tuple[QueueInfo, ...]:
        queues, _diagnostics = self.queues()
        return queues

    def _discover(
        self, host: str
    ) -> tuple[tuple[str, ...], bool]:
        queues = self._host_queues(host)
        if self._cache is None:
            return queues, True
        cached = self._cache.store_host_membership(host, queues)
        if not cached:
            return queues, False
        effective = self._cache.load_host_memberships((host,)).get(
            host
        )
        return (effective if effective is not None else queues), True

    def discover(
        self, hosts: Sequence[str]
    ) -> tuple[dict[str, tuple[str, ...]], tuple[Diagnostic, ...]]:
        if not hosts:
            return {}, ()
        verified: dict[str, tuple[str, ...]] = {}
        failures: list[str] = []
        cache_failures = 0
        futures: dict[Future[tuple[tuple[str, ...], bool]], str] = {}
        executor = ThreadPoolExecutor(
            max_workers=min(_MEMBERSHIP_WORKERS, len(hosts)),
            thread_name_prefix="cad-lsf-membership",
        )
        try:
            futures = {
                executor.submit(self._discover, host): host
                for host in hosts
            }
            for future in as_completed(futures):
                host = futures[future]
                try:
                    queues, cached = future.result()
                    verified[host] = queues
                    if not cached:
                        cache_failures += 1
                except CollectorCancelled:
                    for pending in futures:
                        pending.cancel()
                    raise
                except CollectorError as exc:
                    failures.append(str(exc))
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        diagnostics: list[Diagnostic] = []
        if failures:
            first = failures[0]
            message = (
                f"Failed to verify queue membership for {len(failures)} host(s)"
            )
            if first:
                message += f"; first failure: {first}"
            diagnostics.append(Diagnostic("queue_membership_failed", message))
        if cache_failures:
            diagnostics.append(
                Diagnostic(
                    "topology_cache_write_failed",
                    "Cannot cache queue membership for "
                    f"{cache_failures} host(s); they will be verified again "
                    "on the next refresh",
                )
            )
        return verified, tuple(diagnostics)

    def memberships(self, hosts: Sequence[str]):
        memberships = self._cache.load_host_memberships(hosts) if self._cache is not None else {}
        missing = [host for host in hosts if host not in memberships]
        discovered, diagnostics = self.discover(missing)
        memberships.update(discovered)
        return memberships, diagnostics
