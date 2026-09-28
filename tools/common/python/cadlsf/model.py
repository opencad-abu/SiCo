"""Immutable data contracts for LSF discovery and monitoring."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


SCHEMA_VERSION = 3


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: str = "warning"


@dataclass(frozen=True)
class QueueInfo:
    name: str
    status: str
    is_open: bool
    total_jobs: int | None = None
    pending_jobs: int | None = None
    running_jobs: int | None = None


@dataclass(frozen=True)
class HostInfo:
    name: str
    status: str
    is_available: bool
    max_slots: int | None = None
    total_jobs: int | None = None
    running_jobs: int | None = None
    suspended_jobs: int | None = None
    reserved_slots: int | None = None
    load_1m: float | None = None
    cpu_utilization: float | None = None
    memory_available_bytes: int | None = None
    swap_available_bytes: int | None = None
    unavailable_metrics: tuple[str, ...] = ()
    memory_total_bytes: int | None = None

    @property
    def used_slots(self) -> int | None:
        values = (
            self.running_jobs,
            self.suspended_jobs,
            self.reserved_slots,
        )
        if all(value is None for value in values):
            return self.total_jobs
        return sum(value or 0 for value in values)

    @property
    def free_slots(self) -> int | None:
        used = self.used_slots
        if self.max_slots is None or used is None:
            return None
        return max(0, self.max_slots - used)

    @property
    def slot_utilization(self) -> float | None:
        used = self.used_slots
        if self.max_slots is None or used is None or self.max_slots <= 0:
            return None
        return min(1.0, max(0.0, used / self.max_slots))

    @property
    def memory_utilization(self) -> float | None:
        return self._used_capacity_ratio(
            self.memory_available_bytes, self.memory_total_bytes
        )

    @staticmethod
    def _used_capacity_ratio(
        available: int | None, total: int | None
    ) -> float | None:
        if available is None or total is None or total <= 0:
            return None
        return min(1.0, max(0.0, 1.0 - (available / total)))


@dataclass(frozen=True)
class JobInfo:
    job_id: str
    user: str
    status: str
    queue: str
    first_execution_host: str
    execution_hosts: str
    slots: int | None
    submit_time: str
    start_time: str
    run_time_seconds: int | None
    name: str


@dataclass(frozen=True)
class ClusterSnapshot:
    status: str
    collected_at: str
    user: str
    selected_queue: str | None = None
    queues: tuple[QueueInfo, ...] = ()
    hosts: tuple[HostInfo, ...] = ()
    jobs: tuple[JobInfo, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
