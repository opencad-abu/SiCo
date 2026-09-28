"""Queue, host, and job column definitions for LSF snapshots."""

from __future__ import annotations

from PyQt5.QtCore import QModelIndex, Qt
from ..model import HostInfo, JobInfo, QueueInfo
from ..policy import recommend_hosts
from .model_contracts import Column, ReadOnlyTableModel, SEARCH_ROLE
from .model_formatters import (
    display_integer, display_float, display_percent, display_bytes,
    display_duration, utilization_level,
)

_RIGHT = int(Qt.AlignRight | Qt.AlignVCenter)
_CENTER = int(Qt.AlignCenter)

class QueueTableModel(ReadOnlyTableModel[QueueInfo]):
    columns = (
        Column("Queue", lambda row: row.name, lambda row: row.name.casefold()),
        Column("Status", lambda row: row.status, lambda row: row.status.casefold()),
        Column("RUN", lambda row: display_integer(row.running_jobs), lambda row: row.running_jobs, _RIGHT),
        Column("PEND", lambda row: display_integer(row.pending_jobs), lambda row: row.pending_jobs, _RIGHT),
        Column("Total", lambda row: display_integer(row.total_jobs), lambda row: row.total_jobs, _RIGHT),
    )


class HostTableModel(ReadOnlyTableModel[HostInfo]):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._ranks: dict[str, int] = {}
        self._reasons: dict[str, str] = {}
        self._max_swap = 0

    @property
    def columns(self) -> tuple[Column[HostInfo], ...]:
        return (
            Column("Rank", self._rank_text, self._rank_value, _CENTER),
            Column("Host", lambda row: row.name, lambda row: row.name.casefold()),
            Column("Status", lambda row: row.status, lambda row: row.status.casefold()),
            Column("Slots", self._slot_text, lambda row: row.slot_utilization, _RIGHT, self._slot_progress),
            Column("RUN", lambda row: display_integer(row.running_jobs), lambda row: row.running_jobs, _RIGHT),
            Column("SUSP", lambda row: display_integer(row.suspended_jobs), lambda row: row.suspended_jobs, _RIGHT),
            Column("RSV", lambda row: display_integer(row.reserved_slots), lambda row: row.reserved_slots, _RIGHT),
            Column("r1m", lambda row: display_float(row.load_1m), lambda row: row.load_1m, _RIGHT),
            Column("CPU", lambda row: display_percent(row.cpu_utilization), lambda row: row.cpu_utilization, _RIGHT, self._cpu_progress),
            Column("Memory Used", self._memory_text, lambda row: row.memory_utilization, _RIGHT, self._memory_progress, self._memory_tooltip),
            Column("Swap Avail", lambda row: display_bytes(row.swap_available_bytes), lambda row: row.swap_available_bytes, _RIGHT, self._swap_progress),
            Column("Recommendation", self._reason, self._rank_value),
        )

    def set_rows(self, rows: tuple[HostInfo, ...]) -> None:
        recommendations = recommend_hosts(tuple(rows))
        self._ranks = {
            item.host.name: index for index, item in enumerate(recommendations, 1)
        }
        self._reasons = {
            item.host.name: ", ".join(item.reasons) for item in recommendations
        }
        self._max_swap = max(
            (row.swap_available_bytes or 0 for row in rows), default=0
        )
        super().set_rows(rows)

    def _rank_value(self, row: HostInfo) -> int | None:
        return self._ranks.get(row.name)

    def _rank_text(self, row: HostInfo) -> str:
        rank = self._rank_value(row)
        return "-" if rank is None else str(rank)
    def _reason(self, row: HostInfo) -> str:
        return self._reasons.get(row.name, "Unavailable")

    @staticmethod
    def _slot_text(row: HostInfo) -> str:
        used = row.used_slots
        if used is None or row.max_slots is None:
            return "-"
        return f"{used} / {row.max_slots}"

    def _slot_progress(self, row: HostInfo) -> tuple[float | None, str]:
        return row.slot_utilization, self._slot_text(row)

    @staticmethod
    def _cpu_progress(row: HostInfo) -> tuple[float | None, str, str]:
        ratio = row.cpu_utilization
        return ratio, display_percent(ratio), utilization_level(ratio)

    @staticmethod
    def _memory_text(row: HostInfo) -> str:
        ratio = row.memory_utilization
        if ratio is not None:
            return display_percent(ratio)
        return display_bytes(row.memory_available_bytes)

    @staticmethod
    def _memory_tooltip(row: HostInfo) -> str:
        available = display_bytes(row.memory_available_bytes)
        total = display_bytes(row.memory_total_bytes)
        if row.memory_utilization is None:
            return f"{available} available; total memory unavailable"
        return f"{available} available of {total} total"

    def _memory_progress(
        self, row: HostInfo
    ) -> tuple[float | None, str, str] | None:
        ratio = row.memory_utilization
        if ratio is None:
            return None
        return ratio, self._memory_text(row), utilization_level(ratio)

    def _swap_progress(self, row: HostInfo) -> tuple[float | None, str]:
        ratio = None
        if row.swap_available_bytes is not None and self._max_swap > 0:
            ratio = row.swap_available_bytes / self._max_swap
        return ratio, display_bytes(row.swap_available_bytes)


class JobTableModel(ReadOnlyTableModel[JobInfo]):
    columns = (
        Column(
            "Job ID",
            lambda row: row.job_id,
            lambda row: (
                0, int(row.job_id)
            ) if row.job_id.isdigit() else (1, row.job_id.casefold()),
        ),
        Column("Status", lambda row: row.status, lambda row: row.status.casefold()),
        Column("Queue", lambda row: row.queue, lambda row: row.queue.casefold()),
        Column(
            "Host",
            lambda row: row.first_execution_host or "-",
            lambda row: row.first_execution_host.casefold(),
            tooltip=lambda row: row.execution_hosts or "Not allocated",
        ),
        Column(
            "Slots",
            lambda row: display_integer(row.slots),
            lambda row: row.slots,
            _RIGHT,
        ),
        Column(
            "Runtime",
            lambda row: display_duration(row.run_time_seconds),
            lambda row: row.run_time_seconds,
            _RIGHT,
        ),
        Column(
            "Submit Time",
            lambda row: row.submit_time or "-",
            lambda row: row.submit_time,
        ),
        Column(
            "Start Time",
            lambda row: row.start_time or "-",
            lambda row: row.start_time,
        ),
        Column("Job Name", lambda row: row.name, lambda row: row.name.casefold()),
        Column("Action", lambda _row: "Kill", lambda row: row.job_id, _CENTER),
    )

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        value = super().data(index, role)
        if (
            role == SEARCH_ROLE
            and index.isValid()
            and 0 <= index.row() < len(self.rows)
        ):
            row = self.rows[index.row()]
            return " ".join(
                (
                    row.job_id,
                    row.status,
                    row.queue,
                    row.first_execution_host,
                    row.execution_hosts,
                    row.submit_time,
                    row.start_time,
                    row.name,
                    row.user,
                )
            )
        return value
