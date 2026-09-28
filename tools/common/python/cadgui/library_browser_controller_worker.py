"""Qt worker that bridges one catalog Future to controller signals.

The worker owns only the blocking wait and cooperative cancellation hook.
Controller state, generation gating, and manager lifetime remain in
``library_browser_controller``.
"""

from __future__ import annotations

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot

from cadview.manager import CatalogRequest


class _CatalogFutureSignals(QObject):
    """Signals emitted by a single Future waiter."""

    completed = pyqtSignal(int, object)
    cancelled = pyqtSignal(int)
    failed = pyqtSignal(int, str)


class _CatalogFutureWorker(QRunnable):
    """Wait for one manager Future without blocking the GUI thread."""

    def __init__(self, token: int, request: CatalogRequest) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.token = token
        self.request = request
        self.signals = _CatalogFutureSignals()

    def cancel(self) -> None:
        """Forward cancellation to the domain request."""

        self.request.cancel()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self.request.future.result()
        except Exception as exc:
            if self.request.cancelled:
                self.signals.cancelled.emit(self.token)
            else:
                self.signals.failed.emit(
                    self.token,
                    f"{type(exc).__name__}: {exc}",
                )
        else:
            if self.request.cancelled:
                self.signals.cancelled.emit(self.token)
            else:
                self.signals.completed.emit(self.token, result)


__all__ = ["_CatalogFutureSignals", "_CatalogFutureWorker"]
