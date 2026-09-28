"""Cancelable background validation for LSF selector Apply actions."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event

from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot

from ..collector import (
    CollectorCancelled,
    CollectorConfig,
    CollectorError,
    LsfCollector,
    SubprocessRunner,
)


ValidationCollectorFactory = Callable[[CollectorConfig, Event], LsfCollector]


def _collector_factory(
    config: CollectorConfig, cancel_event: Event
) -> LsfCollector:
    return LsfCollector(
        config=config,
        runner=SubprocessRunner(cancelled=cancel_event.is_set),
    )


class SelectionValidationWorkerSignals(QObject):
    succeeded = pyqtSignal(int, str, object)
    cancelled = pyqtSignal(int)
    failed = pyqtSignal(int, str)


class SelectionValidationWorker(QRunnable):
    def __init__(
        self,
        token: int,
        config: CollectorConfig,
        queue: str,
        host: str | None,
        collector_factory: ValidationCollectorFactory,
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.token = token
        self.config = config
        self.queue = queue
        self.host = host
        self.signals = SelectionValidationWorkerSignals()
        self.cancel_event = Event()
        self._collector_factory = collector_factory

    def cancel(self) -> None:
        self.cancel_event.set()

    @pyqtSlot()
    def run(self) -> None:
        if self.cancel_event.is_set():
            self.signals.cancelled.emit(self.token)
            return
        try:
            collector = self._collector_factory(self.config, self.cancel_event)
            collector.validate_selection(self.queue, self.host)
        except CollectorCancelled:
            self.signals.cancelled.emit(self.token)
        except CollectorError as exc:
            self.signals.failed.emit(self.token, str(exc))
        except Exception:
            # Do not expose arbitrary command/runtime details through Virtuoso.
            self.signals.failed.emit(
                self.token, "Unexpected error while validating the LSF selection"
            )
        else:
            if self.cancel_event.is_set():
                self.signals.cancelled.emit(self.token)
            else:
                self.signals.succeeded.emit(
                    self.token, self.queue, self.host
                )


class SelectionValidationController(QObject):
    """Run one live selector validation outside the Qt GUI thread."""

    busyChanged = pyqtSignal(bool)
    succeeded = pyqtSignal(str, object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        config: CollectorConfig,
        parent: QObject | None = None,
        *,
        collector_factory: ValidationCollectorFactory = _collector_factory,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._collector_factory = collector_factory
        self._generation = 0
        self._worker: SelectionValidationWorker | None = None
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busyChanged.emit(value)

    def validate(self, queue: str, host: str | None = None) -> bool:
        if self._worker is not None:
            return False
        self._generation += 1
        worker = SelectionValidationWorker(
            self._generation,
            self.config,
            queue,
            host,
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
        self._generation += 1
        worker = self._worker
        if worker is not None:
            worker.cancel()
            if self.pool.tryTake(worker):
                self._worker = None
        completed = self.pool.waitForDone(timeout_ms)
        self._worker = None
        self._set_busy(False)
        return completed

    def _finish(self, token: int) -> bool:
        if self._worker is not None and self._worker.token == token:
            self._worker = None
        current = token == self._generation
        if current:
            self._set_busy(False)
        return current

    def _succeeded(self, token: int, queue: str, host: object) -> None:
        if self._finish(token):
            self.succeeded.emit(queue, host)

    def _cancelled(self, token: int) -> None:
        self._finish(token)

    def _failed(self, token: int, message: str) -> None:
        if self._finish(token):
            self.failed.emit(message)


__all__ = [
    "SelectionValidationController",
    "SelectionValidationWorker",
    "SelectionValidationWorkerSignals",
    "ValidationCollectorFactory",
]
