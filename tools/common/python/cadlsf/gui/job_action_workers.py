"""One explicit, cancellable LSF job action at a time."""

from __future__ import annotations

from threading import Event
from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot
from ..collector import CollectorCancelled, CollectorConfig
from .worker_collector import CollectorFactory, create_collector

class JobActionWorkerSignals(QObject):
    succeeded = pyqtSignal(str)
    cancelled = pyqtSignal(str)
    failed = pyqtSignal(str, str)


class JobActionWorker(QRunnable):
    def __init__(
        self,
        job_id: str,
        config: CollectorConfig,
        collector_factory: CollectorFactory,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.job_id = job_id
        self.config = config
        self.signals = JobActionWorkerSignals()
        self.cancel_event = Event()
        self._collector_factory = collector_factory

    def cancel(self) -> None:
        self.cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        if self.cancel_event.is_set():
            self.signals.cancelled.emit(self.job_id)
            return
        try:
            collector = self._collector_factory(self.config, self.cancel_event)
            collector.kill_job(self.job_id)
        except CollectorCancelled:
            self.signals.cancelled.emit(self.job_id)
        except Exception as exc:
            self.signals.failed.emit(
                self.job_id, f"{type(exc).__name__}: {exc}"
            )
        else:
            if self.cancel_event.is_set():
                self.signals.cancelled.emit(self.job_id)
            else:
                self.signals.succeeded.emit(self.job_id)


class JobActionController(QObject):
    """Run one explicit LSF job action at a time outside the GUI thread."""

    busyChanged = pyqtSignal(bool)
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str, str)

    def __init__(
        self,
        config: CollectorConfig,
        parent: QObject | None = None,
        *,
        collector_factory: CollectorFactory = create_collector,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._collector_factory = collector_factory
        self._worker: JobActionWorker | None = None
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busyChanged.emit(value)

    def kill_job(self, job_id: str) -> bool:
        if self._worker is not None:
            return False
        worker = JobActionWorker(
            job_id,
            self.config,
            self._collector_factory,
        )
        worker.signals.succeeded.connect(self._succeeded)
        worker.signals.cancelled.connect(self._cancelled)
        worker.signals.failed.connect(self._failed)
        self._worker = worker
        self._set_busy(True)
        self.pool.start(worker)
        return True

    def shutdown(self, timeout_ms: int = 2_000) -> bool:
        worker = self._worker
        if worker is not None:
            worker.cancel()
            if self.pool.tryTake(worker):
                self._worker = None
        completed = self.pool.waitForDone(timeout_ms)
        self._worker = None
        self._set_busy(False)
        return completed

    def _finish(self) -> None:
        self._worker = None
        self._set_busy(False)

    def _succeeded(self, job_id: str) -> None:
        self._finish()
        self.succeeded.emit(job_id)

    def _cancelled(self, _job_id: str) -> None:
        self._finish()

    def _failed(self, job_id: str, message: str) -> None:
        self._finish()
        self.failed.emit(job_id, message)
