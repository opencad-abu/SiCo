"""LSF collection use cases composed from command, topology and sampling owners.

Compatibility exports retain the historical collector imports. They alias their
owning modules and can be removed when external consumers use those modules;
CLI/GUI callers continue to use LsfCollector's public operations.
"""

from __future__ import annotations

from typing import Sequence

from .cache import MonitorTopologyCache, SessionTopologyCache
from .capacity import CapacityReader, HostCapacityCache
from .command_runner import (
    CollectorCancelled, CollectorError, CommandResult, CommandRunner,
    STDOUT_LIMIT_BYTES, STDERR_LIMIT_BYTES, SubprocessRunner,
)
from .commands import CollectorConfig, LsfCommands, current_os_user, validate_resource_name
from .model import ClusterSnapshot, Diagnostic, HostInfo, JobInfo, QueueInfo
from .monitor_topology import MonitorTopology
from .parsers import (parse_queues, parse_hosts, parse_load, parse_host_capacities,
                      parse_queue_names, parse_jobs)
from .sampling import LsfSampler, MonitorDynamicSample, RefreshData
from .session_topology import SessionTopology
from .snapshots import (collected_now, merge_hosts, monitor_snapshot,
                        select_queue, session_diagnostics, snapshot_status)


class LsfCollector:
    def __init__(
        self, *, config: CollectorConfig | None = None,
        runner: CommandRunner | None = None, user: str | None = None,
        topology_cache: SessionTopologyCache | None = None,
        capacity_cache: HostCapacityCache | None = None,
    ) -> None:
        commands = self._commands = LsfCommands(
            config or CollectorConfig(), runner or SubprocessRunner(), user or current_os_user(),
        )
        self._session = SessionTopology(commands.queues, commands.host_queues, topology_cache)
        capacity = CapacityReader(commands.capacities, capacity_cache or HostCapacityCache())
        self._sampler = LsfSampler(
            queues=self._session.queues, hosts=commands.hosts, load=commands.load,
            capacities=capacity.collect, jobs=commands.jobs,
        )
        self._monitor = MonitorTopology(
            queues=self._session.open_queues, host_queues=commands.host_queues,
            capacities=commands.capacities,
        )

    def collect_queues(self) -> tuple[QueueInfo, ...]:
        return self._session.open_queues()

    def collect_jobs(self) -> tuple[JobInfo, ...]:
        return self._commands.jobs()

    def collect_host_capacities_live(self) -> dict[str, int | None]:
        """Collect capacities without consulting the process-memory cache."""
        return self._commands.capacities()

    def kill_job(self, job_id: str) -> None:
        self._commands.kill_job(job_id)

    def validate_selection(self, queue: str, host: str | None = None) -> None:
        """Check Apply against current permissions, independently of cache age."""
        selected = validate_resource_name(queue, "queue")
        if selected not in {item.name for item in self.collect_queues() if item.is_open}:
            raise CollectorError(
                "Selected queue is no longer accessible and open for the current user"
            )
        if host is None:
            return
        selected_host = validate_resource_name(host, "host")
        host_row = next((item for item in self._commands.hosts() if item.name == selected_host), None)
        if host_row is None or not host_row.is_available:
            raise CollectorError("Selected host is no longer available")
        if selected not in self._commands.host_queues(selected_host):
            raise CollectorError("Selected host is no longer in the selected queue")

    def _session_hosts(self, selected, hosts, loads, load_error, capacities):
        memberships, membership_diagnostics = self._session.memberships(
            [host.name for host in hosts if host.is_available],
        )
        merged, load_diagnostics = merge_hosts(
            selected, hosts, loads, load_error, memberships, capacities,
            preserve_missing_load=True,
        )
        return merged, load_diagnostics + membership_diagnostics

    def collect_hosts(self, queue: str) -> tuple[tuple[HostInfo, ...], tuple[Diagnostic, ...]]:
        selected = validate_resource_name(queue, "queue")
        hosts, loads, load_error = self._sampler.hosts_and_load()
        return self._session_hosts(selected, hosts, loads, load_error, {})

    @staticmethod
    def _requested_queue(queue):
        if queue is None:
            return None, ()
        try:
            return validate_resource_name(queue, "queue"), ()
        except CollectorError as exc:
            return None, (Diagnostic("invalid_queue", str(exc), "error"),)

    def collect_monitor_dynamic_sample(self) -> MonitorDynamicSample:
        return MonitorDynamicSample(self._sampler.monitor_dynamic(), collected_now())

    def monitor_topology_refresh(
        self, cache: MonitorTopologyCache, host_names: Sequence[str], *, force_topology: bool = False,
    ) -> tuple[Diagnostic, ...]:
        return self._monitor.refresh(cache, host_names, force_topology=force_topology)

    def monitor_snapshot(
        self, cache: MonitorTopologyCache, queue: str | None = None, *,
        dynamic_sample: MonitorDynamicSample | None = None,
    ) -> ClusterSnapshot:
        requested, diagnostics = self._requested_queue(queue)
        sample = dynamic_sample if dynamic_sample is not None else self.collect_monitor_dynamic_sample()
        return monitor_snapshot(
            user=self._commands.user, queue=queue, requested=requested,
            dynamic=sample.data, collected_at=sample.collected_at,
            queues=cache.load_queues().value or (),
            memberships=cache.load_host_memberships().value or {},
            capacities=cache.load_host_capacities().value or {}, diagnostics=diagnostics,
        )

    def snapshot(
        self, queue: str | None = None, *, include_hosts: bool = True, include_jobs: bool = False,
    ) -> ClusterSnapshot:
        requested, invalid = self._requested_queue(queue)
        sample = RefreshData()
        if include_hosts and not invalid:
            sample = self._sampler.refresh(include_jobs=include_jobs)
        elif not invalid:
            try:
                queues, diagnostics = self._session.queues()
                sample = RefreshData(queues=queues, queue_diagnostics=diagnostics)
            except CollectorError as exc:
                sample = RefreshData(queue_error=exc)
        diagnostics = list(invalid + session_diagnostics(sample))
        queues = () if sample.queue_error is not None else sample.queues
        selected, selection_diagnostics = select_queue(
            queue, requested, queues, choose_default=include_hosts,
        )
        diagnostics.extend(selection_diagnostics)
        hosts = ()
        if include_hosts and selected is not None:
            if sample.host_error is not None:
                diagnostics.append(Diagnostic("host_collection_failed", str(sample.host_error), "error"))
            else:
                hosts, host_diagnostics = self._session_hosts(
                    selected, sample.hosts, sample.loads or {}, sample.load_error, sample.capacities or {},
                )
                diagnostics.extend(host_diagnostics)
        return ClusterSnapshot(
            status=snapshot_status(queue, selected, queues, hosts, sample.jobs, diagnostics),
            collected_at=collected_now(), user=self._commands.user, selected_queue=selected,
            queues=queues, hosts=hosts, jobs=sample.jobs, diagnostics=tuple(diagnostics),
        )
