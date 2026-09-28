"""Translate one cell's visible form to/from immutable draft values."""

from __future__ import annotations
from PyQt5.QtCore import Qt
from ..model_entries import CornerExport
from .cell_drafts import CellDraft, CellIdentity, SimulatorDraft, PublicationSelection


class CellForm:
    def __init__(self, simulator, publication, models, options, corners, *,
                 update_title, save, changed, has_active):
        self._simulator = simulator
        self._publication = publication
        self._models = models
        self._options = options
        self._corners = corners
        self._update_title = update_title
        self._save = save
        self._changed = changed
        self._has_active = has_active
        self.loading = False
        self.dialect = "spectre"

    def capture(self, key, *, dialect=None) -> CellDraft | None:
        if key is None:
            return None

        def value_or_none(spin):
            return None if spin.value() == spin.minimum() else spin.value()

        return CellDraft(
            CellIdentity(*key), dialect or self.dialect,
            SimulatorDraft(
                models=self._models.rows(), temp=self._simulator.temp.text().strip(),
                scale=self._simulator.scale.text().strip(), gmin=self._simulator.gmin.text().strip(),
                tnom=value_or_none(self._simulator.tnom), scalem=value_or_none(self._simulator.scalem),
                reltol=value_or_none(self._simulator.reltol), options=self._options.rows(),
                temperature_mode=self._simulator.temperature_mode.currentData(),
                corner_export=self._corners.value(),
            ),
            PublicationSelection(
                target_library=self._publication.target_library.currentText(),
                target_cell=self._publication.target_cell.text().strip(),
                publish_symbol=self._publication.publish_symbol.isChecked(),
                overwrite_symbol=self._publication.overwrite_symbol_view.isChecked(),
                publish_text=self._publication.publish_text.isChecked(),
                overwrite_text=self._publication.overwrite_netlist_view.isChecked(),
            ),
        )

    def clear(self) -> None:
        self.loading = True
        try:
            self.dialect = "spectre"
            self._corners.set_value(CornerExport())
            self._simulator.temperature_mode.setCurrentIndex(0)
            self._models.table.setRowCount(0)
            self._options.table.setRowCount(0)
            self._simulator.simulator.setCurrentText("spectre")
            for spin in (
                self._simulator.temp,
                self._simulator.tnom,
                self._simulator.scale,
                self._simulator.scalem,
                self._simulator.reltol,
                self._simulator.gmin,
            ):
                spin.setValue(spin.minimum())
            self._publication.target_cell.clear()
            self._publication.target_library.setCurrentIndex(-1)
            self._publication.publish_symbol.setChecked(False)
            self._publication.publish_text.setChecked(False)
            self._publication.overwrite_symbol_view.setChecked(False)
            self._publication.overwrite_netlist_view.setChecked(False)
        finally:
            self.loading = False
        self._update_title()

    def load(self, draft: CellDraft) -> None:
        self.loading = True
        try:
            state, publication = draft.simulator, draft.publication
            self.dialect = draft.dialect
            self._simulator.simulator.setCurrentText(draft.dialect)
            self._corners.set_value(state.corner_export)
            self._simulator.temperature_mode.setCurrentIndex(self._simulator.temperature_mode.findData(state.temperature_mode))
            self._simulator.temp.setText(state.temp)
            self._simulator.scale.setText(state.scale)
            self._simulator.gmin.setText(state.gmin)
            self._simulator.tnom.setValue(self._simulator.tnom.minimum() if state.tnom is None else state.tnom)
            self._simulator.scalem.setValue(self._simulator.scalem.minimum() if state.scalem is None else state.scalem)
            self._simulator.reltol.setValue(self._simulator.reltol.minimum() if state.reltol is None else state.reltol)
            self._models.set_rows(state.models)
            self._options.set_rows(state.options)
            self.set_target_library(publication.target_library)
            self._publication.target_cell.setText(publication.target_cell)
            self._publication.publish_symbol.setChecked(publication.publish_symbol)
            self._publication.overwrite_symbol_view.setChecked(publication.overwrite_symbol)
            self._publication.publish_text.setChecked(publication.publish_text)
            self._publication.overwrite_netlist_view.setChecked(publication.overwrite_text)
        finally:
            self.loading = False
        self._update_title()
        self.update_target_controls()

    def set_target_library(self, library: str) -> None:
        """Show a configured target name without silently substituting one."""

        value = str(library).strip()
        if not value:
            self._publication.target_library.setCurrentIndex(-1)
            return
        index = self._publication.target_library.findText(value, Qt.MatchExactly)
        if index < 0:
            self._publication.target_library.addItem(value)
            index = self._publication.target_library.count() - 1
            self._publication.target_library.setItemData(
                index,
                "Not present in the current authoritative target catalog",
                Qt.ToolTipRole,
            )
        self._publication.target_library.setCurrentIndex(index)

    def publication_requested(self) -> bool:
        return self._publication.publish_symbol.isChecked() or self._publication.publish_text.isChecked()

    def update_target_controls(self, _signal_value=None) -> None:
        enabled = self.publication_requested()
        for widget in self._publication._target_widgets:
            widget.setEnabled(enabled)
        if hasattr(self._publication, "overwrite_symbol_view"):
            symbol_enabled = self._publication.publish_symbol.isChecked()
            self._publication.overwrite_symbol_view.setEnabled(symbol_enabled)
            if not symbol_enabled:
                self._publication.overwrite_symbol_view.setChecked(False)
        if hasattr(self._publication, "overwrite_netlist_view"):
            text_enabled = self._publication.publish_text.isChecked()
            self._publication.overwrite_netlist_view.setEnabled(text_enabled)
            if not text_enabled:
                self._publication.overwrite_netlist_view.setChecked(False)
        if not self.loading and self._has_active():
            self._save()

    def temperature_mode_changed(self, _signal_value=None) -> None:
        self._simulator.temp.setEnabled(self._simulator.temperature_mode.currentData() == "fixed")
        self._changed()

    def update_export_labels(self) -> None:
        if hasattr(self._publication, "publish_text"):
            library = self._corners.value().mode == "library"
            self._publication.publish_text.setText("Generate model-binding view + corner library" if library else "Generate netlist view")
            self._models.table.setEnabled(not library)
