"""Cancelable snapshot collection with only the latest request published."""

from __future__ import annotations

from dataclasses import replace
from threading import Event
from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot
from ..collector import (
    CollectorCancelled, CollectorConfig, HostCapacityCache, LsfCollector,
    SubprocessRunner,
)
from ..cache import MonitorTopologyCache
from .worker_collector import CollectorFactory, create_collector

class RefreshWorkerSignals(QObject):
    cached = pyqtSignal(int, object)
    completed = pyqtSignal(int, object)
    cancelled = pyqtSignal(int)
    failed = pyqtSignal(int, str)


class RefreshWorker(QRunnable):
    def __init__(
        self,
        token: int,
        config: CollectorConfig,
        queue: str | None,
        collector_factory: CollectorFactory,
        topology_cache: MonitorTopologyCache | None = None,
        force_topology: bool = False,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.token = token
        self.config = config
        self.queue = queue
        self.signals = RefreshWorkerSignals()
        self.cancel_event = Event()
        self._collector_factory = collector_factory
        self.topology_cache = topology_cache
        self.force_topology = force_topology

    def cancel(self) -> None:
        self.cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        if self.cancel_event.is_set():
            self.signals.cancelled.emit(self.token)
            return
        try:
            collector = self._collector_factory(self.config, self.cancel_event)
            if self.topology_cache is None:
                snapshot = collector.snapshot(self.queue, include_jobs=True)
            else:
                dynamic_sample = collector.collect_monitor_dynamic_sample()
                cached = collector.monitor_snapshot(
                    self.topology_cache,
                    self.queue,
                    dynamic_sample=dynamic_sample,
                )
                if self.cancel_event.is_set():
                    self.signals.cancelled.emit(self.token)
                    return
                self.signals.cached.emit(self.token, cached)
                topology_diagnostics = collector.monitor_topology_refresh(
                    self.topology_cache,
                    dynamic_sample.available_host_names,
                    force_topology=self.force_topology,
                )
                if self.cancel_event.is_set():
                    self.signals.cancelled.emit(self.token)
                    return
                snapshot = collector.monitor_snapshot(
                    self.topology_cache,
                    self.queue,
                    dynamic_sample=dynamic_sample,
                )
                if topology_diagnostics:
                    snapshot = replace(
                        snapshot,
                        status=(
                            "partial"
                            if snapshot.status != "error"
                            else snapshot.status
                        ),
                        diagnostics=(
                            snapshot.diagnostics + topology_diagnostics
                        ),
                    )
        except CollectorCancelled:
            self.signals.cancelled.emit(self.token)
        except Exception as exc:
            self.signals.failed.emit(
                self.token, f"{type(exc).__name__}: {exc}"
            )
        else:
            if self.cancel_event.is_set():
                self.signals.cancelled.emit(self.token)
            else:
                self.signals.completed.emit(self.token, snapshot)


class RefreshController(QObject):
    """Collect snapshots in worker threads and publish only the newest request."""

    busyChanged = pyqtSignal(bool)
    snapshotReady = pyqtSignal(object)
    cachedSnapshotReady = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        config: CollectorConfig,
        parent: QObject | None = None,
        *,
        collector_factory: CollectorFactory = create_collector,
        topology_cache: MonitorTopologyCache | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.topology_cache = topology_cache
        capacity_cache = HostCapacityCache()
        if collector_factory is create_collector:
            self._collector_factory = lambda config, cancel_event: LsfCollector(
                config=config,
                runner=SubprocessRunner(cancelled=cancel_event.is_set),
                capacity_cache=capacity_cache,
            )
        else:
            self._collector_factory = collector_factory
        self._generation = 0
        self._workers: dict[int, RefreshWorker] = {}
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def generation(self) -> int:
        return self._generation

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busyChanged.emit(value)

    def submit(self, queue: str | None, force_topology: bool = False) -> int:
        self._generation += 1
        token = self._generation
        self._cancel_workers()
        worker = RefreshWorker(
            token,
            self.config,
            queue,
            self._collector_factory,
            self.topology_cache,
            force_topology,
        )
        worker.signals.completed.connect(self._completed)
        worker.signals.cached.connect(self._cached)
        worker.signals.cancelled.connect(self._cancelled)
        worker.signals.failed.connect(self._failed)
        self._workers[token] = worker
        self._set_busy(True)
        self.pool.start(worker)
        return token

    def invalidate(self) -> None:
        self._generation += 1
        self._cancel_workers()
        self._set_busy(False)

    def shutdown(self, timeout_ms: int = 2_000) -> bool:
        self.invalidate()
        return self.pool.waitForDone(timeout_ms)

    def _cancel_workers(self) -> None:
        for token, worker in tuple(self._workers.items()):
            worker.cancel()
            if self.pool.tryTake(worker):
                self._workers.pop(token, None)

    def _completed(self, token: int, snapshot: object) -> None:
        self._workers.pop(token, None)
        if token == self._generation:
            self._set_busy(False)
            self.snapshotReady.emit(snapshot)

    def _cached(self, token: int, snapshot: object) -> None:
        if token == self._generation:
            self.cachedSnapshotReady.emit(snapshot)

    def _cancelled(self, token: int) -> None:
        self._workers.pop(token, None)
        if token == self._generation:
            self._set_busy(False)

    def _failed(self, token: int, message: str) -> None:
        self._workers.pop(token, None)
        if token == self._generation:
            self._set_busy(False)
            self.failed.emit(message)
