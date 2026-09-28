"""Replace one process source context and apply its validated workspace request."""

from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QListWidgetItem
from ..workspace_model import WorkspaceCellPresentation
from ..model_request import NetlistRequest
from ..model_design import SourceDesign
from .draft_requests import draft_from_spec


class ProcessContext:
    def __init__(self, *, form, source, source_edit, selection, queue, browser, legacy,
                 presenter, drafts, defaults, cancel_defaults, reset_worker, invalidate, refresh):
        self._form = form
        self._source = source
        self._source_edit = source_edit
        self._selection = selection
        self._queue = queue
        self._browser = browser
        self._legacy = legacy
        self._presenter = presenter
        self.drafts = drafts
        self.defaults = defaults
        self._cancel_defaults = cancel_defaults
        self._reset_worker = reset_worker
        self._invalidate = invalidate
        self._refresh = refresh
        self.startup_file = None
        self.simrc = None

    def changed(self, *_args) -> None:
        if self._form.loading:
            return
        raw = self._source_edit.text().strip()
        new_path = Path(raw).expanduser().resolve() if raw else None
        old_path = self._source.path
        has_context = bool(
            self._queue.count()
            or self.drafts
            or self.defaults.baselines
            or self._selection.active is not None
            or self.startup_file is not None
            or self.simrc is not None
            or self._presenter.source_catalog is not None
            or any(
                field.text().strip()
                for field in (self._legacy[0], self._legacy[1], self._legacy[2])
            )
        )
        self._source.path = new_path
        if old_path is not None and new_path != old_path and has_context:
            self.clear()
        self._invalidate("source cds.lib changed")

    def clear(self) -> None:
        """Drop all source-owned state when the source cds.lib changes."""

        self._cancel_defaults()
        self._reset_worker()
        self._form.loading = True
        try:
            self._selection.active = None
            self.drafts.clear()
            self.defaults.reset()
            self._queue.clear()
            self.startup_file = None
            self.simrc = None
            self._browser.clear()
            self._presenter.source_catalog = None
            self._legacy[0].clear()
            self._legacy[1].clear()
            self._legacy[2].clear()
            self._form.clear()
        finally:
            self._form.loading = False
        # An empty queue must not fall back to the legacy hidden fields after
        # a source-context switch; the user must select a view from the new
        # catalog first.
        self._selection.queue_mode = True

    def apply(
        self,
        request: NetlistRequest,
        *,
        refresh_catalog: bool = True,
        presentation: tuple[WorkspaceCellPresentation, ...] = (),
        project: str = "",
    ) -> None:
        value = request.validate()
        presentation_map = {
            item.key: (
                item.temperature_text,
                item.scale_text,
                item.gmin_text,
            )
            for item in (entry.validate() for entry in presentation)
        }
        if self._source.is_session_path(value.source.cds_lib):
            # Validate before mutating any widgets/state so a rejected config
            # cannot discard a previously selected external source context.
            self._source.warn(value.source.cds_lib)
            raise ValueError(self._source.warning_text(value.source.cds_lib))
        self._source.select_project(project)
        if self._source.project is not None:
            expected_default = self._source.project.cds_lib.resolve()
            configured_source = value.source.cds_lib.resolve()
            if configured_source == expected_default:
                value = replace(
                    value,
                    source=replace(value.source, cds_lib=expected_default),
                )
        self._cancel_defaults()
        self._invalidate("configuration loaded")
        self._form.loading = True
        try:
            self._source.path = value.source.cds_lib.expanduser().resolve()
            self._source_edit.setText(str(value.source.cds_lib))
            self._selection.active = None
            self._selection.queue_mode = True
            self.startup_file = value.source.startup_file
            self.simrc = value.source.simrc
            self._presenter.source_catalog = None
            self._browser.clear()
            self.drafts.clear()
            self.defaults.reset()
            self._queue.clear()
            specs = value.selected_cells
            for spec in specs:
                key = (spec.library, spec.cell, spec.view)
                state = draft_from_spec(spec, value.dialect, presentation_map.get(key))
                self.drafts.add(state.key, state.dialect, state.simulator, state.publication)
                item = QListWidgetItem("/".join(state.key))
                item.setData(Qt.UserRole, state.key)
                item.setToolTip("/".join(state.key))
                self._queue.addItem(item)
            if refresh_catalog:
                self._refresh()
            has_cells = bool(self._queue.count())
            if not has_cells:
                self._form.clear()
        finally:
            self._form.loading = False
        # Let the normal selection signal load the first cell after the full
        # context replacement guard is released.
        if self._queue.count():
            self._queue.setCurrentRow(0)

    def design(self, key) -> SourceDesign:
        return SourceDesign(
            Path(self._source_edit.text().strip()), *key,
            self.startup_file, self.simrc,
        )
