"""Edit model rows and their section selectors without owning cell drafts."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from cadgui.prompts import ask_file
from PyQt5.QtWidgets import QComboBox, QTableWidgetItem
from ..model_corners import model_corners
from .form_state import duplicate_row, move_row, remove_rows


class ModelTable:
    def __init__(self, table, changed, dialog_parent) -> None:
        self.table = table
        self._changed = changed
        self._dialog_parent = dialog_parent

    def add(self, _signal_value=None) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.set_row(row, enabled=True)
        self.table.selectRow(row)

    def remove(self, _signal_value=None) -> None:
        rows = self.rows()
        selected = {index.row() for index in self.table.selectedIndexes()}
        self.set_rows(remove_rows(rows, tuple(selected)))
        self._changed()

    def duplicate(self, _signal_value=None) -> None:
        row = self.table.currentRow()
        rows = self.rows()
        if not 0 <= row < len(rows):
            return
        self.set_rows(duplicate_row(rows, row), selected=row + 1)
        self._changed()

    def browse(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        path, _ = ask_file(self._dialog_parent, "Select model file", ["Model files (*)"])
        if path:
            self.table.blockSignals(True)
            try:
                self.table.setItem(row, 1, QTableWidgetItem(path))
                self.populate_corners(row, path)
            finally:
                self.table.blockSignals(False)
            self._changed()

    def cell_double_clicked(self, row: int, column: int) -> None:
        if column != 1:
            return
        self.table.setCurrentCell(row, column)
        self.browse()

    @staticmethod
    def corners(path: str) -> tuple[str, ...]:
        return model_corners(path)

    def corner_widget(self, row: int, value: str = "") -> QComboBox:
        combo = QComboBox(self.table)
        combo.setEditable(True)
        combo.addItem(value)
        combo.setCurrentText(value)
        combo.currentTextChanged.connect(lambda _text, r=row: self.corner_changed(r))
        self.table.setCellWidget(row, 2, combo)
        return combo

    def populate_corners(self, row: int, path: str, selected: str = "") -> None:
        combo = self.corner_widget(row, selected)
        corners = self.corners(path)
        if corners:
            combo.clear()
            combo.addItems(corners)
            if selected and selected not in corners:
                combo.addItem(selected)
            combo.setCurrentText(selected or corners[0])

    def corner_changed(self, _row: int) -> None:
        # The editable combo is the source of truth; _request reads it when
        # constructing the typed ModelEntry.
        self._changed()

    def cell_changed(self, row: int, column: int) -> None:
        if column != 1:
            return
        item = self.table.item(row, column)
        if item is not None and item.text().strip():
            self.populate_corners(row, item.text().strip())

    def set_row(
        self,
        row: int,
        *,
        enabled: bool,
        file: str = "",
        section: str = "",
        label: str = "",
        corner: str | None = None,
    ) -> None:
        # ``section`` is the stable config/model field.  ``corner`` is the UI
        # spelling and an optional compatibility keyword for callers.
        if corner is not None:
            section = corner
        enabled_item = QTableWidgetItem()
        enabled_item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
        self.table.setItem(row, 0, enabled_item)
        self.table.setItem(row, 1, QTableWidgetItem(file))
        self.populate_corners(row, file, section) if file else self.corner_widget(row, section)
        self.table.setItem(row, 3, QTableWidgetItem(label))

    def cell_text(self, row: int, column: int) -> str:
        widget = self.table.cellWidget(row, column)
        if isinstance(widget, QComboBox):
            return widget.currentText()
        item = self.table.item(row, column)
        return item.text() if item is not None else ""

    def rows(self) -> tuple[tuple[bool, str, str, str], ...]:
        rows = []
        for row in range(self.table.rowCount()):
            enabled = self.table.item(row, 0)
            values = [self.cell_text(row, column) for column in (1, 2, 3)]
            rows.append(
                (
                    enabled is None or enabled.checkState() == Qt.Checked,
                    *values,
                )
            )
        return tuple(rows)

    def set_rows(
        self, rows: tuple[tuple[bool, str, str, str], ...], selected: int | None = None
    ) -> None:
        self.table.setRowCount(0)
        for row, (enabled, file, section, label) in enumerate(rows):
            self.table.insertRow(row)
            self.set_row(row, enabled=enabled, file=file, section=section, label=label)
        if selected is not None and 0 <= selected < len(rows):
            self.table.selectRow(selected)

    def move(self, delta: int) -> None:
        row = self.table.currentRow()
        rows = self.rows()
        if not 0 <= row < len(rows):
            return
        moved = move_row(rows, row, delta)
        target = max(0, min(len(rows) - 1, row + delta))
        self.set_rows(moved, selected=target)
        self._changed()

    def move_up(self) -> None:
        self.move(-1)

    def move_down(self) -> None:
        self.move(1)
