"""Cancelable background jobs used by the DSPF GUI."""

from __future__ import annotations

from threading import Event
from typing import Any, Callable

from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot


class IndexWorkerSignals(QObject):
    started = pyqtSignal()
    progress = pyqtSignal(object)
    completed = pyqtSignal(object)
    cancelled = pyqtSignal()
    failed = pyqtSignal(str)


class IndexWorker(QRunnable):
    """Run ``build_index`` without blocking the Qt event loop."""

    def __init__(
        self,
        source: str,
        *,
        cache_dir: str | None = None,
        force: bool = False,
        build: Callable | None = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.cache_dir = cache_dir
        self.force = force
        self.signals = IndexWorkerSignals()
        self._cancel_event = Event()
        self._build = build

    def cancel(self) -> None:
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @pyqtSlot()
    def run(self) -> None:
        from rcepy.dspf.indexer import IndexCancelled, build_index

        operation = self._build or build_index
        if self.is_cancelled():
            self.signals.cancelled.emit()
            return
        self.signals.started.emit()
        try:
            result = operation(
                self.source,
                cache_dir=self.cache_dir,
                force=self.force,
                progress=self.signals.progress.emit,
                cancelled=self.is_cancelled,
            )
        except IndexCancelled:
            self.signals.cancelled.emit()
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            if self.is_cancelled():
                self.signals.cancelled.emit()
            else:
                self.signals.completed.emit(result)


class IndexController(QObject):
    busyChanged = pyqtSignal(bool)
    progress = pyqtSignal(object)
    completed = pyqtSignal(object)
    cancelled = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.worker: IndexWorker | None = None

    @property
    def busy(self) -> bool:
        return self.worker is not None

    def start(self, source: str, *, cache_dir: str | None, force: bool) -> None:
        if self.worker is not None:
            raise RuntimeError("a DSPF index job is already running")
        worker = IndexWorker(source, cache_dir=cache_dir, force=force)
        self.worker = worker
        worker.signals.progress.connect(self.progress)
        worker.signals.completed.connect(self._completed)
        worker.signals.cancelled.connect(self._cancelled)
        worker.signals.failed.connect(self._failed)
        self.busyChanged.emit(True)
        self.pool.start(worker)

    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()

    def shutdown(self, timeout_ms: int = 2_000) -> None:
        self.cancel()
        self.pool.waitForDone(timeout_ms)

    def _finish(self) -> None:
        self.worker = None
        self.busyChanged.emit(False)

    def _completed(self, result: object) -> None:
        self._finish()
        self.completed.emit(result)

    def _cancelled(self) -> None:
        self._finish()
        self.cancelled.emit()

    def _failed(self, message: str) -> None:
        self._finish()
        self.failed.emit(message)


class QueryWorkerSignals(QObject):
    completed = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)


class QueryWorker(QRunnable):
    def __init__(self, token: int, operation: Callable[[], Any]) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.token = token
        self.operation = operation
        self.signals = QueryWorkerSignals()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self.operation()
        except Exception as exc:
            self.signals.failed.emit(self.token, f"{type(exc).__name__}: {exc}")
        else:
            self.signals.completed.emit(self.token, result)


class QueryController(QObject):
    """Run the latest expensive query and discard stale selection results."""

    busyChanged = pyqtSignal(bool)
    resultReady = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._generation = 0
        self._workers: dict[int, QueryWorker] = {}

    def submit(self, operation: Callable[[], Any]) -> int:
        self._drop_queued()
        self._generation += 1
        token = self._generation
        worker = QueryWorker(token, operation)
        worker.signals.completed.connect(self._completed)
        worker.signals.failed.connect(self._failed)
        self._workers[token] = worker
        self.busyChanged.emit(True)
        self.pool.start(worker)
        return token

    def invalidate(self) -> None:
        self._generation += 1
        self._drop_queued()
        self.busyChanged.emit(False)

    def shutdown(self, timeout_ms: int = 2_000) -> None:
        self.invalidate()
        self.pool.waitForDone(timeout_ms)

    def _drop_queued(self) -> None:
        for token, worker in tuple(self._workers.items()):
            if self.pool.tryTake(worker):
                self._workers.pop(token, None)

    def _completed(self, token: int, result: object) -> None:
        self._workers.pop(token, None)
        if token == self._generation:
            self.busyChanged.emit(False)
            self.resultReady.emit(result)

    def _failed(self, token: int, message: str) -> None:
        self._workers.pop(token, None)
        if token == self._generation:
            self.busyChanged.emit(False)
            self.failed.emit(message)


__all__ = [
    "IndexController",
    "IndexWorker",
    "IndexWorkerSignals",
    "QueryController",
    "QueryWorker",
]
