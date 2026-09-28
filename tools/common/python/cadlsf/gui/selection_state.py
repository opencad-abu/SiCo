"""Immutable selector view of the current snapshot and Qt selection."""

from __future__ import annotations

from dataclasses import dataclass

from ..model import ClusterSnapshot, HostInfo


@dataclass(frozen=True)
class SelectionState:
    queue: str
    host: str | None
    queue_eligible: bool
    host_eligible: bool
    closing: bool

    def fingerprint(self, host_required: bool) -> tuple[str, str | None]:
        return self.queue, self.host if host_required else None


def selection_state(
    snapshot: ClusterSnapshot | None,
    *,
    queue: str,
    host: HostInfo | None,
    busy: bool,
    status: str,
    closing: bool,
) -> SelectionState:
    queue_eligible = bool(
        not closing
        and not busy
        and snapshot is not None
        and status in {"ready", "partial"}
        and snapshot.status in {"ready", "partial"}
        and snapshot.selected_queue == queue
        and any(item.name == queue and item.is_open for item in snapshot.queues)
    )
    host_eligible = bool(
        queue_eligible
        and host is not None
        and host.is_available
        and any(item.name == host.name and item.is_available for item in snapshot.hosts)
    )
    return SelectionState(
        queue,
        host.name if host is not None else None,
        queue_eligible,
        host_eligible,
        closing,
    )
