"""Windowed Qt table models backed by :class:`DspfRepository`."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
import math
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt5.QtGui import QColor


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    aliases: tuple[str, ...] = ()
    sortable: bool = False


NET_COLUMNS = (
    Column("id", "ID", sortable=True),
    Column("name", "Net", sortable=True),
    Column("declared_cap", "Declared C", ("declared_capacitance",), True),
    Column("line_start", "Start Line", sortable=True),
    Column("line_end", "End Line"),
)
NODE_COLUMNS = (
    Column("name", "Node", ("node_name",)),
    Column("kind", "Kind"),
    Column("x", "X"),
    Column("y", "Y"),
    Column("layer", "Layer"),
    Column("source_line", "Source Line"),
)
RESISTOR_COLUMNS = (
    Column("name", "Resistor"),
    Column("node1", "Node 1", ("node1_name", "node1_id")),
    Column("node2", "Node 2", ("node2_name", "node2_id")),
    Column("value", "Resistance (ohm)"),
    Column("layer", "Layer"),
    Column("length", "Length"),
    Column("width", "Width"),
    Column("source_line", "Source Line"),
)
CAPACITOR_COLUMNS = (
    Column("name", "Capacitor"),
    Column("node1", "Node 1", ("node1_name", "node1_id")),
    Column("node2", "Node 2", ("node2_name", "node2_id")),
    Column("value", "Capacitance (F)"),
    Column("layer", "Layer"),
    Column("source_line", "Source Line"),
)
DIAGNOSTIC_COLUMNS = (
    Column("severity", "Severity"),
    Column("code", "Code"),
    Column("message", "Message", ("summary",)),
    Column("source_line", "Source Line", ("line",)),
)


def _default_repository(path: str | Path):
    from rcepy.dspf.repository import DspfRepository

    return DspfRepository(path)


def _mapping(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    if is_dataclass(row):
        return asdict(row)
    if hasattr(row, "to_dict"):
        return dict(row.to_dict())
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    raise TypeError(f"repository row is not mapping-like: {type(row).__name__}")


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if not math.isfinite(value):
            return str(value)
        if value == 0:
            return "0"
        return f"{value:.6g}"
    return str(value)


class PagedRepositoryModel(QAbstractTableModel):
    """Keep exactly one SQL page in memory and replace it on navigation."""

    pageChanged = pyqtSignal(int, int, int)
    loadFailed = pyqtSignal(str)

    def __init__(
        self,
        columns: tuple[Column, ...],
        count_method: str,
        list_method: str,
        *,
        page_size: int = 200,
        count_keys: tuple[str, ...] = (),
        list_keys: tuple[str, ...] = (),
        repository_factory: Callable = _default_repository,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        self.columns = columns
        self.count_method = count_method
        self.list_method = list_method
        self.page_size = page_size
        self.count_keys = count_keys
        self.list_keys = list_keys
        self.repository_factory = repository_factory
        self.index_path: Path | None = None
        self.query: dict[str, Any] = {}
        self.rows: list[dict[str, Any]] = []
        self.total_rows = 0
        self.page = 0

    @property
    def page_count(self) -> int:
        return max(1, math.ceil(self.total_rows / self.page_size))

    def set_index(self, index_path: str | Path | None, *, reload: bool = True) -> None:
        self.index_path = Path(index_path) if index_path else None
        self.page = 0
        if reload:
            self.reload()
        else:
            self._replace([], 0)

    def set_query(self, **query: Any) -> None:
        self.query.update(query)
        self.page = 0
        self.reload()

    def set_page(self, page: int) -> None:
        bounded = max(0, min(int(page), self.page_count - 1))
        if bounded == self.page and self.rows:
            return
        self.page = bounded
        self.reload()

    def reload(self) -> None:
        if self.index_path is None:
            self._replace([], 0)
            return
        count_args = {key: self.query.get(key) for key in self.count_keys}
        list_args = {key: self.query.get(key) for key in self.list_keys}
        list_args.update(offset=self.page * self.page_size, limit=self.page_size)
        try:
            with self.repository_factory(self.index_path) as repository:
                total = int(getattr(repository, self.count_method)(**count_args))
                raw_rows = getattr(repository, self.list_method)(**list_args)
                rows = [_mapping(row) for row in raw_rows]
        except Exception as exc:
            self.loadFailed.emit(f"{type(exc).__name__}: {exc}")
            self._replace([], 0)
            return
        if total and self.page >= math.ceil(total / self.page_size):
            self.page = max(0, math.ceil(total / self.page_size) - 1)
            self.reload()
            return
        self._replace(rows, total)

    def _replace(self, rows: list[dict[str, Any]], total: int) -> None:
        self.beginResetModel()
        self.rows = rows
        self.total_rows = total
        self.endResetModel()
        self.pageChanged.emit(self.page + 1, self.page_count, self.total_rows)

    def row_at(self, row: int) -> dict[str, Any] | None:
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.column() < len(self.columns):
            return None
        row = self.row_at(index.row())
        if row is None:
            return None
        column = self.columns[index.column()]
        value = next((row[key] for key in (column.key, *column.aliases) if key in row), None)
        if role == Qt.UserRole:
            return row
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            return _display(value)
        if role == Qt.TextAlignmentRole and isinstance(value, (int, float)):
            return Qt.AlignRight | Qt.AlignVCenter
        if role == Qt.ForegroundRole and column.key == "severity":
            severity = str(value).casefold()
            if severity == "error":
                return QColor("#b42318")
            if severity in {"warning", "warn"}:
                return QColor("#9a6700")
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(self.columns):
            return self.columns[section].title
        return str(self.page * self.page_size + section + 1)

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        if not 0 <= column < len(self.columns) or not self.columns[column].sortable:
            return
        self.query["sort_by"] = self.columns[column].key
        self.query["descending"] = order == Qt.DescendingOrder
        self.page = 0
        self.reload()


def create_models() -> dict[str, PagedRepositoryModel]:
    nets = PagedRepositoryModel(
        NET_COLUMNS,
        "count_nets",
        "list_nets",
        count_keys=("search", "exact_name"),
        list_keys=("search", "exact_name", "sort_by", "descending"),
    )
    nets.query.update(search=None, exact_name=None, sort_by="name", descending=False)
    result = {"nets": nets}
    for name, columns in (
        ("nodes", NODE_COLUMNS),
        ("resistors", RESISTOR_COLUMNS),
        ("capacitors", CAPACITOR_COLUMNS),
        ("diagnostics", DIAGNOSTIC_COLUMNS),
    ):
        result[name] = PagedRepositoryModel(
            columns,
            f"count_{name}",
            f"list_{name}",
            count_keys=("net",),
            list_keys=("net",),
        )
        result[name].query["net"] = None
    return result


__all__ = [
    "CAPACITOR_COLUMNS",
    "DIAGNOSTIC_COLUMNS",
    "NET_COLUMNS",
    "NODE_COLUMNS",
    "PagedRepositoryModel",
    "RESISTOR_COLUMNS",
    "create_models",
]
