"""Accept controller snapshots once and present their catalog/generation results."""

from __future__ import annotations
from pathlib import Path
from PyQt5.QtCore import QTimer
from ..generation_result import MultiGenerationResult
from .controller import ControllerState


class ProcessPresenter:
    def __init__(self, *, controller, session, defaults, defaults_ui, generation, results,
                 diagnostics, logs, source, source_text, form, selection, drafts, target,
                 browser, publish_queued, error, append, show_status, enable_run):
        self._controller = controller
        self._session = session
        self.defaults = defaults
        self._defaults_ui = defaults_ui
        self.generation = generation
        self._results = results
        self._diagnostics = diagnostics
        self._logs = logs
        self._source = source
        self._source_text = source_text
        self._form = form
        self._selection = selection
        self.drafts = drafts
        self._target = target
        self._browser = browser
        self._publish_queued = publish_queued
        self._error = error
        self._append = append
        self._show_status = show_status
        self._enable_run = enable_run
        self.pending = None
        self.source_catalog = None
        self.target_catalog = None
        self.target_authoritative = False
        self.publication = None

    def clear_source(self):
        self.source_catalog = None
        self._browser.clear()

    def receive(self, state: ControllerState) -> None:
        self.pending = state

    def poll(self) -> None:
        self._logs.drain()
        state = self.pending
        if state is None:
            return
        self.pending = None
        if not state.busy:
            self._logs.drain(flush_tail=True)
        self._show_status(state.stage)
        self.defaults.receive(state)
        self._defaults_ui.refresh_form()
        if state.error:
            self._error(state.error)
        # ControllerState deliberately carries the last successful catalogs
        # through generation and publication.  Rebuilding a selector for that
        # unchanged object clears its current selection, which used to erase
        # source library/cell/view immediately after generation and made the
        # following Publish fail validation with an empty source library.
        source_catalog = state.source_catalog
        current_source = self._source_text().strip()
        catalog_path = getattr(getattr(source_catalog, "catalog", None), "cds_library_file", None)
        catalog_matches = (
            source_catalog is not None
            and catalog_path is not None
            and current_source
            and Path(catalog_path).expanduser().resolve()
            == Path(current_source).expanduser().resolve()
        )
        if source_catalog is not None and not catalog_matches:
            self._append("Ignored source catalog for a different cds.lib context")
        if source_catalog is not None and catalog_matches and source_catalog is not self.source_catalog:
            self._source.path = Path(catalog_path).expanduser().resolve()
            self._diagnostics.source_catalog(source_catalog)
            self.source_tree(state.source_catalog)
            self.source_catalog = state.source_catalog
            if self._session() is not None and self._source.target_refresh_requested:
                self._source.target_refresh_requested = False
                self._controller().refresh_target_catalog()
        if (
            state.target_catalog is not None
            and state.target_catalog is not self.target_catalog
        ):
            self.target_libraries(state.target_catalog)
            self.target_catalog = state.target_catalog
        accepted = self.generation.receive(state)
        if accepted:
            self._results.generated(self.generation.result, request=self.generation.request)
            if isinstance(self.generation.result, MultiGenerationResult):
                for item in self.generation.result.cells:
                    self._append(f"Generated [{item.cell}]: {item.result.stable_output}")
            else:
                self._append(f"Generated: {self.generation.result.stable_output}")
        elif accepted is False:
            self._append("Discarded stale generation result; source context changed")
        if (
            state.publication is not None
            and state.publication is not self.publication
        ):
            self.publication = state.publication
            if isinstance(state.publication, tuple):
                for result in state.publication:
                    self._diagnostics.publication(result)
            else:
                self._diagnostics.publication(state.publication)
            self._results.published(state.publication)
        if accepted and self.generation.handoff is not None:
            QTimer.singleShot(0, self._publish_queued)
        if not state.busy and self.generation.handoff is None:
            self._defaults_ui.advance()
        self._enable_run(
            not self._controller().state.busy and not self.defaults.busy
            and self.generation.handoff is None)
        self._defaults_ui.update_cursor()

    def source_tree(self, result) -> None:
        if not result.authoritative:
            self._append("Warning: source catalog is a non-authoritative filesystem preview")
        self._browser.set_catalog(result.catalog)

    def target_libraries(self, result) -> None:
        selected = self._target.currentText()
        self._target.blockSignals(True)
        self._target.clear()
        self.target_authoritative = bool(result.authoritative)
        if not result.authoritative:
            self._append("Warning: target catalog is non-authoritative; publication remains locked")
        for library in result.catalog.libraries:
            if library.writable:
                self._target.addItem(library.name)
        if selected:
            self._form.set_target_library(selected)
        if self._selection.active is not None:
            state = self.drafts.view(self._selection.active, self._form.dialect)
            if state.publication.target_library:
                self._form.set_target_library(state.publication.target_library)
        self._target.blockSignals(False)
        self._form.update_target_controls()
