"""Concurrent collection of independent LSF samples."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Mapping

from .command_runner import CollectorError
from .model import Diagnostic, HostInfo, JobInfo, QueueInfo


@dataclass(frozen=True)
class RefreshData:
    queues: tuple[QueueInfo, ...] = ()
    queue_diagnostics: tuple[Diagnostic, ...] = ()
    queue_error: CollectorError | None = None
    hosts: tuple[HostInfo, ...] = ()
    loads: Mapping[str, tuple[object, ...]] | None = None
    capacities: Mapping[str, int | None] | None = None
    host_error: CollectorError | None = None
    load_error: CollectorError | None = None
    jobs: tuple[JobInfo, ...] = ()
    job_error: CollectorError | None = None


@dataclass(frozen=True)
class MonitorDynamicSample:
    data: RefreshData
    collected_at: str

    @property
    def available_host_names(self) -> tuple[str, ...]:
        return tuple(host.name for host in self.data.hosts if host.is_available)


class LsfSampler:
    def __init__(self, *, queues: Callable, hosts: Callable, load: Callable,
                 capacities: Callable, jobs: Callable):
        self._queues = queues
        self._hosts = hosts
        self._load = load
        self._capacities = capacities
        self._jobs = jobs

    def hosts_and_load(
        self,
    ) -> tuple[
        tuple[HostInfo, ...],
        dict[str, tuple[object, ...]],
        CollectorError | None,
    ]:
        """Collect the independent host and load samples concurrently."""
        executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="cad-lsf-sample",
        )
        host_future = executor.submit(self._hosts)
        load_future = executor.submit(self._load)
        host_error: CollectorError | None = None
        load_error: CollectorError | None = None
        hosts: tuple[HostInfo, ...] = ()
        loads: dict[str, tuple[object, ...]] = {}
        try:
            try:
                hosts = host_future.result()
            except CollectorError as exc:
                host_error = exc
            try:
                loads = load_future.result()
            except CollectorError as exc:
                load_error = exc
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        if host_error is not None:
            raise host_error
        return hosts, loads, load_error

    def refresh(
        self, *, include_jobs: bool = False
    ) -> RefreshData:
        """Run independent Queue, Host, Load, and optional Job samples."""
        executor = ThreadPoolExecutor(
            max_workers=5 if include_jobs else 3,
            thread_name_prefix="cad-lsf-refresh",
        )
        queue_future = executor.submit(self._queues)
        host_future = executor.submit(self._hosts)
        load_future = executor.submit(self._load)
        capacity_future = (
            executor.submit(self._capacities)
            if include_jobs
            else None
        )
        job_future = executor.submit(self._jobs) if include_jobs else None
        queues: tuple[QueueInfo, ...] = ()
        queue_diagnostics: tuple[Diagnostic, ...] = ()
        queue_error: CollectorError | None = None
        hosts: tuple[HostInfo, ...] = ()
        loads: dict[str, tuple[object, ...]] = {}
        host_error: CollectorError | None = None
        load_error: CollectorError | None = None
        capacities: dict[str, int | None] = {}
        jobs: tuple[JobInfo, ...] = ()
        job_error: CollectorError | None = None
        try:
            try:
                queues, queue_diagnostics = queue_future.result()
            except CollectorError as exc:
                queue_error = exc
            try:
                hosts = host_future.result()
            except CollectorError as exc:
                host_error = exc
            try:
                loads = load_future.result()
            except CollectorError as exc:
                load_error = exc
            if capacity_future is not None:
                try:
                    capacities = capacity_future.result()
                except CollectorError:
                    pass
            if job_future is not None:
                try:
                    jobs = job_future.result()
                except CollectorError as exc:
                    job_error = exc
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        return RefreshData(
            queues=queues,
            queue_diagnostics=queue_diagnostics,
            queue_error=queue_error,
            hosts=hosts,
            loads=loads,
            capacities=capacities,
            host_error=host_error,
            load_error=load_error,
            jobs=jobs,
            job_error=job_error,
        )

    def monitor_dynamic(self) -> RefreshData:
        """Collect only data which changes on every Monitor refresh."""
        executor = ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="cad-lsf-monitor"
        )
        host_future = executor.submit(self._hosts)
        load_future = executor.submit(self._load)
        job_future = executor.submit(self._jobs)
        hosts: tuple[HostInfo, ...] = ()
        loads: dict[str, tuple[object, ...]] = {}
        jobs: tuple[JobInfo, ...] = ()
        host_error: CollectorError | None = None
        load_error: CollectorError | None = None
        job_error: CollectorError | None = None
        try:
            try:
                hosts = host_future.result()
            except CollectorError as exc:
                host_error = exc
            try:
                loads = load_future.result()
            except CollectorError as exc:
                load_error = exc
            try:
                jobs = job_future.result()
            except CollectorError as exc:
                job_error = exc
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        return RefreshData(
            hosts=hosts,
            loads=loads,
            host_error=host_error,
            load_error=load_error,
            jobs=jobs,
            job_error=job_error,
        )
