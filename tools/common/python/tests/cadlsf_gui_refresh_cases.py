"""LSF GUI refresh behavior cases."""

from __future__ import annotations

from cadlsf_gui_fixtures import application as application, _snapshot
from pathlib import Path
from threading import Event
import time
from PyQt5.QtWidgets import QApplication
from cadlsf.collector import CollectorConfig
from cadlsf.cache import MonitorTopologyCache
from cadlsf.gui.workers import RefreshController
from cadlsf.model import ClusterSnapshot


def test_refresh_controller_discards_stale_result(application: QApplication) -> None:
    snapshots = {
        "slow": _snapshot("normal"),
        "fast": _snapshot("batch"),
    }

    class FakeCollector:
        def __init__(self, cancel_event: Event) -> None:
            self.cancel_event = cancel_event

        def snapshot(
            self, queue: str | None, *, include_jobs: bool = False
        ) -> ClusterSnapshot:
            if queue == "slow":
                for _ in range(60):
                    if self.cancel_event.is_set():
                        break
                    time.sleep(0.005)
            return snapshots[queue or "fast"]

    def factory(_config: CollectorConfig, cancel_event: Event) -> FakeCollector:
        return FakeCollector(cancel_event)

    controller = RefreshController(
        CollectorConfig(), collector_factory=factory
    )
    received: list[ClusterSnapshot] = []
    controller.snapshotReady.connect(received.append)
    controller.submit("slow")
    controller.submit("fast")
    deadline = time.monotonic() + 3
    while not received and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)
    controller.shutdown()

    assert [snapshot.selected_queue for snapshot in received] == ["batch"]


def test_refresh_controller_publishes_cached_snapshot_before_topology_refresh(
    application: QApplication, tmp_path: Path
) -> None:
    events: list[str] = []
    cached_snapshot = _snapshot()
    refreshed_snapshot = _snapshot("batch")

    class FakeCollector:
        def collect_monitor_dynamic_sample(self):
            events.append("dynamic")

            class Sample:
                available_host_names = ("node-busy", "node-idle")

            return Sample()

        def monitor_snapshot(
            self,
            _cache,
            _queue,
            *,
            dynamic_sample,
        ) -> ClusterSnapshot:
            assert dynamic_sample is not None
            events.append("snapshot")
            return cached_snapshot if events.count("snapshot") == 1 else refreshed_snapshot

        def monitor_topology_refresh(
            self, _cache, _hosts, *, force_topology: bool
        ) -> tuple[()]:
            events.append("topology")
            assert not force_topology
            return ()

    def factory(_config: CollectorConfig, _cancel: Event) -> FakeCollector:
        return FakeCollector()

    cache = MonitorTopologyCache(
        tmp_path / ".cad" / "lsf" / "1001",
        user="demo",
        command_signature=("bqueues",),
    )
    controller = RefreshController(
        CollectorConfig(), collector_factory=factory, topology_cache=cache
    )
    received: list[tuple[str, ClusterSnapshot]] = []
    controller.cachedSnapshotReady.connect(
        lambda snapshot: received.append(("cached", snapshot))
    )
    controller.snapshotReady.connect(
        lambda snapshot: received.append(("final", snapshot))
    )

    controller.submit("normal")
    deadline = time.monotonic() + 2
    while len(received) < 2 and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    controller.shutdown()

    assert [kind for kind, _snapshot_value in received] == ["cached", "final"]
    assert events == ["dynamic", "snapshot", "topology", "snapshot"]
