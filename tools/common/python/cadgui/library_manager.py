"""Standalone Qt Library Manager for Cadence library catalogs.

This module is the optional composition layer for applications that need a
ready-to-use library manager rather than two independent primitives.  It
combines :class:`LibraryBrowserWidget` (presentation) with
:class:`CatalogController` (asynchronous catalog loading), while deliberately
stopping at catalog selection.  It does not start a target Virtuoso session,
write OA data, or know anything about MTS publication.

Applications with source/target isolation requirements can inject a
``CatalogController`` configured with an adapter provider.  This keeps the
generic manager reusable without allowing it to accidentally inspect the
active target session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QCloseEvent
from PyQt5.QtWidgets import QVBoxLayout, QWidget

from cadview.catalog import Catalog, CatalogCell, CatalogLibrary, CatalogView

from .library_browser import CatalogSelection, LibraryBrowserWidget
from .library_browser_controller import CatalogController


class LibraryManagerWidget(QWidget):
    """A reusable, asynchronous ``cds.lib`` browser widget.

    ``LibraryManagerWidget`` owns a :class:`CatalogController` by default and
    shuts it down when the widget is closed.  A caller may inject an existing
    controller, in which case the controller remains caller-owned unless
    ``owns_controller=True`` is explicitly requested.

    The public signals mirror the controller and browser signals.  The
    ``catalogReady`` signal is emitted *after* the browser has received the
    catalog, so consumers can safely inspect ``selection`` in their slot.
    """

    busyChanged = pyqtSignal(bool)
    catalogReady = pyqtSignal(object)
    resultReady = pyqtSignal(object)
    diagnostics = pyqtSignal(object)
    diagnostic = pyqtSignal(str)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal(int)

    selectionChanged = pyqtSignal(object)
    libraryChanged = pyqtSignal(object)
    cellChanged = pyqtSignal(object)
    viewChanged = pyqtSignal(object)
    viewActivated = pyqtSignal(str, str, str)

    def __init__(
        self,
        catalog: Catalog | None = None,
        parent: QObject | None = None,
        *,
        controller: CatalogController | None = None,
        manager=None,
        provider: str | object = "dbAccess",
        providers: Mapping[str, object] | None = None,
        owns_controller: bool | None = None,
        max_workers: int = 2,
        content_top_margin: int = 0,
    ) -> None:
        super().__init__(parent)
        if controller is not None and manager is not None:
            raise ValueError("provide controller or manager, not both")
        if controller is None and owns_controller is False:
            raise ValueError("owns_controller=False requires an injected controller")
        if content_top_margin < 0:
            raise ValueError("content_top_margin must not be negative")

        self.browser = LibraryBrowserWidget(
            catalog=catalog,
            parent=self,
            content_top_margin=content_top_margin,
        )
        # Descriptive aliases make the composition discoverable while keeping
        # the underlying widgets available to host integrations.
        self.library_browser = self.browser
        self.source_browser = self.browser

        if controller is None:
            self.controller = CatalogController(
                manager=manager,
                parent=self,
                provider=provider,
                providers=providers,
                max_workers=max_workers,
            )
            self._owns_controller = True if owns_controller is None else owns_controller
        else:
            self.controller = controller
            self._owns_controller = (
                False if owns_controller is None else bool(owns_controller)
            )
        self.catalog_controller = self.controller
        self._connections_detached = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.browser, 1)

        self.controller.busyChanged.connect(self.busyChanged)
        self.controller.resultReady.connect(self._on_result_ready)
        self.controller.catalogReady.connect(self._on_catalog_ready)
        self.controller.diagnostics.connect(self.diagnostics)
        self.controller.diagnostic.connect(self.diagnostic)
        self.controller.failed.connect(self.failed)
        self.controller.cancelled.connect(self.cancelled)

        self.browser.libraryChanged.connect(self.libraryChanged)
        self.browser.cellChanged.connect(self.cellChanged)
        self.browser.viewChanged.connect(self.viewChanged)
        self.browser.selectionChanged.connect(self.selectionChanged)
        self.browser.viewActivated.connect(self.viewActivated)

    @property
    def catalog(self) -> Catalog | None:
        """Return the catalog currently shown by the browser."""

        return self.browser.catalog

    @property
    def selection(self) -> CatalogSelection | None:
        """Return the selected ``(library, cell, view)`` tuple, if complete."""

        return self.browser.selection

    @property
    def selected_library(self) -> CatalogLibrary | None:
        return self.browser.selected_library

    @property
    def selected_combined_library(self) -> str | None:
        return self.browser.selected_combined_library

    @property
    def selected_cell(self) -> CatalogCell | None:
        return self.browser.selected_cell

    @property
    def selected_view(self) -> CatalogView | None:
        return self.browser.selected_view

    @property
    def busy(self) -> bool:
        return self.controller.busy

    @property
    def is_busy(self) -> bool:
        return self.controller.busy

    @property
    def generation(self) -> int:
        return self.controller.generation

    @property
    def last_result(self):
        return self.controller.last_result

    @property
    def last_error(self) -> str:
        return self.controller.last_error

    @property
    def last_diagnostics(self) -> tuple[str, ...]:
        return self.controller.last_diagnostics

    def set_catalog(
        self,
        catalog: Catalog | None,
        *,
        preserve_selection: bool = True,
    ) -> None:
        """Present a catalog immediately, without starting a provider."""

        self.browser.set_catalog(catalog, preserve_selection=preserve_selection)

    setCatalog = set_catalog

    def clear(self) -> None:
        """Clear the browser content without changing filter text."""

        self.browser.clear()

    def select(self, library: str, cell: str, view: str) -> bool:
        """Select an exact visible library/cell/view path."""

        return self.browser.select(library, cell, view)

    def activate_current_view(self) -> bool:
        return self.browser.activate_current_view()

    activateCurrentView = activate_current_view

    def load_cds_lib(
        self,
        cds_library_file: str | Path,
        **kwargs: object,
    ) -> int:
        """Load a ``cds.lib`` asynchronously and return its freshness token."""

        # Clear stale rows before a new request.  The controller's token gate
        # still prevents a late result from an older request from returning.
        self.browser.clear()
        return self.controller.submit(cds_library_file, **kwargs)

    loadCdsLib = load_cds_lib
    load = load_cds_lib
    refresh = load_cds_lib

    def cancel(self) -> None:
        """Cancel the active catalog request, if any."""

        self.controller.cancel()

    def shutdown(self, timeout_ms: int = 2_000) -> bool:
        """Stop worker activity and optionally close the owned controller.

        ``False`` means an owned controller still has worker activity.  The
        caller must keep this widget alive and call ``shutdown`` again after
        allowing the provider to finish.
        """

        if self._owns_controller:
            return self.controller.shutdown(timeout_ms)
        # An injected controller may be shared by other widgets.  Do not
        # invalidate or cancel it when this non-owning facade closes; only
        # remove this facade's signal subscriptions.  Otherwise closing one
        # view would silently cancel another consumer's catalog request.
        self._detach_connections()
        return True

    def _detach_connections(self) -> None:
        """Disconnect this widget from an externally owned controller/browser."""

        if self._connections_detached:
            return
        pairs = (
            (self.controller.busyChanged, self.busyChanged),
            (self.controller.resultReady, self._on_result_ready),
            (self.controller.catalogReady, self._on_catalog_ready),
            (self.controller.diagnostics, self.diagnostics),
            (self.controller.diagnostic, self.diagnostic),
            (self.controller.failed, self.failed),
            (self.controller.cancelled, self.cancelled),
            (self.browser.libraryChanged, self.libraryChanged),
            (self.browser.cellChanged, self.cellChanged),
            (self.browser.viewChanged, self.viewChanged),
            (self.browser.selectionChanged, self.selectionChanged),
            (self.browser.viewActivated, self.viewActivated),
        )
        for signal, slot in pairs:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                # A host may already have disposed one side of an injected
                # object graph.  Detaching is idempotent in either case.
                pass
        self._connections_detached = True

    def _on_result_ready(self, result: object) -> None:
        self.resultReady.emit(result)

    def _on_catalog_ready(self, catalog: object) -> None:
        # Keep this slot separate from the direct signal connection so the
        # ordering contract (browser first, then composite signal) is explicit.
        if not isinstance(catalog, Catalog):
            self.failed.emit("CatalogController returned an invalid Catalog")
            return
        self.browser.set_catalog(catalog)
        self.catalogReady.emit(catalog)

    def closeEvent(self, event: QCloseEvent) -> None:
        # Do not destroy this widget (and its child controller/signal objects)
        # while a timed-out worker can still emit a queued completion signal.
        # The host may call close() again after cooperative provider shutdown.
        if self.shutdown():
            event.accept()
        else:
            event.ignore()


__all__ = ["LibraryManagerWidget"]
