"""Pure assembly of permission-filtered LSF snapshots from collected evidence."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Mapping, Sequence

from .command_runner import CollectorError
from .model import ClusterSnapshot, Diagnostic, HostInfo, JobInfo, QueueInfo
from .policy import host_sort_key
from .sampling import RefreshData

_LOAD_METRICS = (
    "load_1m", "cpu_utilization", "memory_available_bytes", "swap_available_bytes",
)


def collected_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def snapshot_status(queue, selected, queues, hosts, jobs, diagnostics) -> str:
    if queue is not None and selected is None:
        return "error"
    if diagnostics:
        return "partial" if queues or hosts or jobs else "error"
    return "ready"


def select_queue(queue, requested, queues, *, choose_default: bool):
    if requested is not None:
        if requested in {item.name for item in queues}:
            return requested, ()
        return None, (Diagnostic(
            "queue_not_accessible",
            "Requested queue is not accessible and open for the current user",
            "error",
        ),)
    if queue is None and queues and choose_default:
        return queues[0].name, ()
    return None, ()


def merge_hosts(
    selected: str,
    hosts: tuple[HostInfo, ...],
    loads: Mapping[str, tuple[object, ...]],
    load_error: CollectorError | None,
    memberships: Mapping[str, Sequence[str]],
    capacities: Mapping[str, int | None],
    *,
    preserve_missing_load: bool = False,
) -> tuple[tuple[HostInfo, ...], tuple[Diagnostic, ...]]:
    """Project available members of one queue, retaining missing-metric evidence."""
    diagnostics = (() if load_error is None else (
        Diagnostic("load_collection_failed", str(load_error)),
    ))
    allowed = {host for host, queues in memberships.items() if selected in queues}
    merged = []
    for host in hosts:
        if host.name not in allowed or not host.is_available:
            continue
        load = loads.get(host.name)
        memory_total = capacities.get(host.name)
        unavailable = list(host.unavailable_metrics)
        values = load if load is not None else (None, None, None, None)
        unavailable.extend(name for name, value in zip(_LOAD_METRICS, values) if value is None)
        unavailable = list(dict.fromkeys(unavailable))
        if memory_total is None:
            unavailable.append("memory_total_bytes")
        if preserve_missing_load and load is None:
            # Historical session snapshots retain any metrics already on the row.
            merged.append(replace(host, memory_total_bytes=memory_total,
                                  unavailable_metrics=tuple(unavailable)))
        else:
            load_1m, cpu, memory, swap = values
            merged.append(replace(
                host, load_1m=load_1m, cpu_utilization=cpu,
                memory_available_bytes=memory, memory_total_bytes=memory_total,
                swap_available_bytes=swap,
                unavailable_metrics=tuple(dict.fromkeys(unavailable)),
            ))
    return tuple(sorted(merged, key=host_sort_key)), diagnostics


def monitor_snapshot(
    *, user: str, queue: str | None, requested: str | None,
    dynamic: RefreshData, collected_at: str, queues: Sequence[QueueInfo],
    memberships: Mapping[str, Sequence[str]], capacities: Mapping[str, int | None],
    diagnostics: Sequence[Diagnostic] = (),
) -> ClusterSnapshot:
    """Use cached topology as supplied; never refresh or publish it here."""
    diagnostics = list(diagnostics)
    if dynamic.host_error is not None:
        diagnostics.append(Diagnostic("host_collection_failed", str(dynamic.host_error), "error"))
    if dynamic.job_error is not None:
        diagnostics.append(Diagnostic("job_collection_failed", str(dynamic.job_error)))
    # Explicit queue selection must be open; the historical default uses row 0.
    candidates = tuple(item for item in queues if item.is_open) if requested is not None else queues
    selected, selection_diagnostics = select_queue(queue, requested, candidates, choose_default=True)
    diagnostics.extend(selection_diagnostics)
    hosts = ()
    if selected is not None and dynamic.host_error is None:
        hosts, host_diagnostics = merge_hosts(
            selected, dynamic.hosts, dynamic.loads or {}, dynamic.load_error,
            memberships, capacities,
        )
        diagnostics.extend(host_diagnostics)
    return ClusterSnapshot(
        status=snapshot_status(queue, selected, queues, hosts, dynamic.jobs, diagnostics),
        collected_at=collected_at, user=user, selected_queue=selected,
        queues=tuple(queues), hosts=hosts, jobs=dynamic.jobs, diagnostics=tuple(diagnostics),
    )


def session_diagnostics(sample: RefreshData) -> tuple[Diagnostic, ...]:
    diagnostics = list(sample.queue_diagnostics)
    if sample.queue_error is not None:
        diagnostics.append(Diagnostic("queue_collection_failed", str(sample.queue_error), "error"))
    if sample.job_error is not None:
        diagnostics.append(Diagnostic("job_collection_failed", str(sample.job_error)))
    return tuple(diagnostics)
