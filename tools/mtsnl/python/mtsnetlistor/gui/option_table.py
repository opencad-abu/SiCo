"""Edit ordered simulator option rows without owning cell drafts."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QTableWidgetItem
from .form_state import duplicate_row, move_row, remove_rows


class OptionTable:
    def __init__(self, table, changed) -> None:
        self.table = table
        self._changed = changed

    def add(self, _signal_value=None) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        enabled = QTableWidgetItem()
        enabled.setCheckState(Qt.Checked)
        self.table.setItem(row, 0, enabled)
        self.table.setItem(row, 1, QTableWidgetItem())
        self.table.setItem(row, 2, QTableWidgetItem("string"))
        self.table.setItem(row, 3, QTableWidgetItem())
        self.table.setItem(row, 4, QTableWidgetItem())
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

    def rows(self) -> tuple[tuple[bool, str, str, str, str], ...]:
        rows = []
        for row in range(self.table.rowCount()):
            enabled = self.table.item(row, 0)
            values = [self.table.item(row, column) for column in (1, 2, 3, 4)]
            rows.append(
                (
                    enabled is None or enabled.checkState() == Qt.Checked,
                    *(item.text() if item is not None else "" for item in values),
                )
            )
        return tuple(rows)

    def set_rows(
        self, rows: tuple[tuple[bool, str, str, str, str], ...], selected: int | None = None
    ) -> None:
        self.table.setRowCount(0)
        for row, (enabled, name, value_type, value, enum_values) in enumerate(rows):
            self.table.insertRow(row)
            option_enabled = QTableWidgetItem()
            option_enabled.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
            self.table.setItem(row, 0, option_enabled)
            self.table.setItem(row, 1, QTableWidgetItem(name))
            self.table.setItem(row, 2, QTableWidgetItem(value_type))
            self.table.setItem(row, 3, QTableWidgetItem(value))
            self.table.setItem(row, 4, QTableWidgetItem(enum_values))
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
