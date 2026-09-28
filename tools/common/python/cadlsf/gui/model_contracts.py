"""Shared Qt table roles and read-only row storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar
from PyQt5.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt5.QtGui import QColor

SORT_ROLE = Qt.UserRole
PROGRESS_ROLE = Qt.UserRole + 1
SEARCH_ROLE = Qt.UserRole + 2
AVAILABLE_ROLE = Qt.UserRole + 3
STATUS_ROLE = Qt.UserRole + 4
JOB_ID_ROLE = Qt.UserRole + 5
RowType = TypeVar("RowType")

@dataclass(frozen=True)
class Column(Generic[RowType]):
    title: str
    display: Callable[[RowType], str]
    sort_value: Callable[[RowType], Any]
    alignment: int = int(Qt.AlignLeft | Qt.AlignVCenter)
    progress: Callable[
        [RowType], tuple[float | None, str] | tuple[float | None, str, str] | None
    ] | None = None
    tooltip: Callable[[RowType], str] | None = None


class ReadOnlyTableModel(QAbstractTableModel, Generic[RowType]):
    columns: tuple[Column[RowType], ...] = ()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: tuple[RowType, ...] = ()

    @property
    def rows(self) -> tuple[RowType, ...]:
        return self._rows

    def set_rows(self, rows: tuple[RowType, ...]) -> None:
        self.beginResetModel()
        self._rows = tuple(rows)
        self.endResetModel()
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)
    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            if 0 <= section < len(self.columns):
                return self.columns[section].title
        return None
    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable
    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        if not 0 <= column < len(self.columns):
            return
        value = self.columns[column].sort_value

        def key(row: RowType) -> Any:
            item = value(row)
            if isinstance(item, str):
                item = item.casefold()
            return item

        self.layoutAboutToBeChanged.emit()
        known = [row for row in self._rows if value(row) is not None]
        unknown = [row for row in self._rows if value(row) is None]
        self._rows = tuple(
            sorted(known, key=key, reverse=order == Qt.DescendingOrder) + unknown
        )
        self.layoutChanged.emit()
    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        if not 0 <= index.column() < len(self.columns):
            return None
        row = self._rows[index.row()]
        column = self.columns[index.column()]
        if role == Qt.DisplayRole:
            return column.display(row)
        if role == Qt.ToolTipRole:
            return column.tooltip(row) if column.tooltip is not None else column.display(row)
        if role == SORT_ROLE:
            return column.sort_value(row)
        if role == Qt.TextAlignmentRole:
            return column.alignment
        if role == PROGRESS_ROLE and column.progress is not None:
            return column.progress(row)
        if role == SEARCH_ROLE:
            return " ".join(item.display(row) for item in self.columns)
        if role == AVAILABLE_ROLE:
            return bool(getattr(row, "is_available", True))
        if role == STATUS_ROLE:
            return str(getattr(row, "status", ""))
        if role == JOB_ID_ROLE:
            return str(getattr(row, "job_id", ""))
        if role == Qt.ForegroundRole and not bool(getattr(row, "is_available", True)):
            return QColor("#777d85")
        return None
