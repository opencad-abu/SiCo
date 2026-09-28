"""Cooperative cancellation handle for one asynchronous catalog request."""

from __future__ import annotations

from concurrent.futures import Future
from threading import Event

from .manager_models import CatalogResult


class CatalogRequest:
    """Handle returned by an asynchronous manager request."""

    def __init__(self, future: Future[CatalogResult], cancel_event: Event) -> None:
        self._future = future
        self._cancel_event = cancel_event

    @property
    def future(self) -> Future[CatalogResult]:
        return self._future

    @property
    def done(self) -> bool:
        return self._future.done()

    @property
    def cancelled(self) -> bool:
        """Return whether cooperative cancellation has been requested.

        This is intentionally broader than ``Future.cancelled()``: a running
        dbAccess provider cannot be cancelled by ``Future.cancel()``, so it
        observes the same event and terminates its child process instead.
        """

        return self._cancel_event.is_set()

    @property
    def cancel_requested(self) -> bool:
        """Explicit spelling for the cooperative cancellation state."""

        return self._cancel_event.is_set()

    def cancel(self) -> bool:
        """Request cancellation and cancel a task that has not started."""

        self._cancel_event.set()
        return self._future.cancel()
