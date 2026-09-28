"""Qt adapter for the reusable, Qt-free Cadence library manager.

The domain facade lives in :mod:`cadview.manager`; this module only marshals
one ``LibraryManager`` request through Qt signals.  It deliberately contains
no MTS, project-module, PDK-default, source-cell, or OA-publication policy.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from PyQt5.QtCore import QObject, QThreadPool, pyqtSignal

from cadview.manager import (
    CatalogDiagnostics,
    CatalogRequest,
    CatalogResult,
    LibraryManager,
)

from .library_browser_controller_worker import (
    _CatalogFutureWorker,
)


# Public aliases keep the Qt adapter useful to custom providers/tests without
# requiring them to import an implementation-specific worker type.
CatalogLoadRequest = CatalogRequest
CatalogLoadResult = CatalogResult


class CatalogController(QObject):
    """Publish only the latest asynchronous ``LibraryManager`` request.

    ``catalogReady`` carries the immutable :class:`cadview.catalog.Catalog`.
    Consumers that need provider metadata can subscribe to ``resultReady``;
    non-fatal manager diagnostics are forwarded through ``diagnostics`` and
    ``diagnostic``.  Every callback is token-gated, so a superseded request can
    neither replace the current catalog nor append stale log information.
    """

    busyChanged = pyqtSignal(bool)
    catalogReady = pyqtSignal(object)
    resultReady = pyqtSignal(object)
    diagnostics = pyqtSignal(object)
    diagnostic = pyqtSignal(str)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal(int)

    def __init__(
        self,
        manager: LibraryManager | None = None,
        parent: QObject | None = None,
        *,
        provider: str | object = "dbAccess",
        providers: Mapping[str, object] | None = None,
        owns_manager: bool | None = None,
        max_workers: int = 2,
    ) -> None:
        super().__init__(parent)
        if max_workers <= 0:
            raise ValueError("max_workers must be greater than zero")
        if manager is None and owns_manager is False:
            raise ValueError("owns_manager=False requires an injected manager")
        self.manager = manager or LibraryManager(
            providers=providers, max_workers=max_workers
        )
        self.provider = provider
        self._owns_manager = manager is None if owns_manager is None else owns_manager
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max_workers)
        self._generation = 0
        self._workers: dict[int, _CatalogFutureWorker] = {}
        self._busy = False
        self._closed = False
        self._last_result: CatalogResult | None = None
        self._last_error = ""
        self._last_diagnostics: tuple[str, ...] = ()

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def last_result(self) -> CatalogResult | None:
        return self._last_result

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def last_diagnostics(self) -> tuple[str, ...]:
        return self._last_diagnostics

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busyChanged.emit(value)

    def _cancel_workers(self) -> None:
        for token, worker in tuple(self._workers.items()):
            worker.cancel()
            # Removing a queued waiter is safe only after its manager Future
            # has terminated.  The manager pool may already be executing the
            # provider while this Qt waiter is still queued; dropping it in
            # that state would let waitForDone() report success before the
            # provider has actually stopped.
            if worker.request.done and self.pool.tryTake(worker):
                self._workers.pop(token, None)

    @property
    def is_busy(self) -> bool:
        """Compatibility alias used by host widgets."""

        return self._busy

    def submit(
        self,
        cds_library_file: str | Path,
        *,
        provider: str | object | None = None,
        executable: str = "dbAccess",
        script: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
        environment: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        cancel_event=None,
    ) -> int:
        """Start a manager request and return its latest-result token.

        The environment is copied before submission so a caller cannot mutate
        the source project context while the Cadence provider is starting.
        ``environment`` is an ergonomic alias for the manager's ``environ``.
        """

        if self._closed:
            raise RuntimeError("catalog controller is shut down")
        if environ is not None and environment is not None:
            raise ValueError("provide environment or environ, not both")
        selected_environment = environ if environ is not None else environment
        environment_snapshot = dict(
            os.environ if selected_environment is None else selected_environment
        )
        if not all(
            isinstance(name, str) and isinstance(value, str)
            for name, value in environment_snapshot.items()
        ):
            raise TypeError("catalog environment must map strings to strings")

        self._generation += 1
        token = self._generation
        self._cancel_workers()
        request_kwargs: dict[str, object] = {
            "provider": self.provider if provider is None else provider,
            "executable": executable,
            "script": script,
            "environ": environment_snapshot,
            "timeout": timeout,
        }
        if cancel_event is not None:
            request_kwargs["cancel_event"] = cancel_event
        request = self.manager.submit(
            Path(cds_library_file),
            **request_kwargs,
        )
        worker = _CatalogFutureWorker(token, request)
        worker.signals.completed.connect(self._completed)
        worker.signals.cancelled.connect(self._worker_cancelled)
        worker.signals.failed.connect(self._worker_failed)
        self._workers[token] = worker
        self._last_error = ""
        self._last_diagnostics = ()
        self._set_busy(True)
        self.pool.start(worker)
        return token

    load = submit
    refresh = submit
    request = submit
    loadCatalog = submit

    def cancel(self) -> None:
        """Cooperatively cancel the current provider and invalidate callbacks."""

        active = self._busy or bool(self._workers)
        token = self._generation
        self._generation += 1
        self._cancel_workers()
        self.manager.cancel()
        self._set_busy(False)
        if active:
            self.cancelled.emit(token)

    def invalidate(self) -> None:
        """Invalidate late callbacks without publishing a cancellation event."""

        self._generation += 1
        self._cancel_workers()
        self.manager.cancel()
        self._set_busy(False)

    def shutdown(self, timeout_ms: int = 2_000) -> bool:
        """Stop the adapter and, when owned, close its domain manager.

        ``False`` means that at least one QRunnable was still running when the
        timeout expired.  Such workers remain in ``_workers`` and a later call
        to ``shutdown`` may continue waiting for them.  This is intentional:
        a timed-out close must not discard the last Python references to a
        worker that can still emit a queued Qt signal.

        Controller methods are expected to be called from the QObject's owning
        (normally GUI) thread.  Worker completion is marshalled back to that
        thread by Qt's signal connection.
        """

        if timeout_ms < 0:
            raise ValueError("shutdown timeout must not be negative")
        if not self._closed:
            self._closed = True
            self.invalidate()
        completed = self.pool.waitForDone(timeout_ms)
        # Keep running workers reachable after a timed-out wait.  Their
        # completion slots remove them once the queued signal is delivered;
        # clearing only after a successful wait avoids late signals targeting
        # an already-forgotten QRunnable.
        if completed:
            self._workers.clear()
        if self._owns_manager:
            self.manager.close(wait=completed)
        return completed

    def _is_current(self, token: int) -> bool:
        return token == self._generation

    def _completed(self, token: int, result: CatalogResult) -> None:
        self._workers.pop(token, None)
        if not self._is_current(token):
            return
        self._last_result = result
        messages = tuple(result.diagnostics.messages)
        self._last_diagnostics = messages
        self._last_error = ""
        self._set_busy(False)
        if messages:
            self.diagnostics.emit(messages)
            for message in messages:
                self.diagnostic.emit(message)
        self.resultReady.emit(result)
        self.catalogReady.emit(result.catalog)

    def _worker_cancelled(self, token: int) -> None:
        self._workers.pop(token, None)
        if self._is_current(token):
            self._set_busy(False)
            self.cancelled.emit(token)

    def _worker_failed(self, token: int, message: str) -> None:
        self._workers.pop(token, None)
        if not self._is_current(token):
            return
        self._last_error = message
        self._set_busy(False)
        self.failed.emit(message)


LibraryBrowserController = CatalogController
LibraryCatalogController = CatalogController


__all__ = [
    "CatalogController",
    "CatalogDiagnostics",
    "CatalogLoadRequest",
    "CatalogLoadResult",
    "LibraryBrowserController",
    "LibraryCatalogController",
]
