"""Host and job filtering with missing values sorted last."""

from __future__ import annotations

from PyQt5.QtCore import QModelIndex, QSortFilterProxyModel, Qt
from .model_contracts import AVAILABLE_ROLE, SEARCH_ROLE, SORT_ROLE, STATUS_ROLE

class HostFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._query = ""
        self._available_only = True
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(True)

    def set_query(self, text: str) -> None:
        query = text.strip().casefold()
        if query != self._query:
            self._query = query
            self.invalidateFilter()

    def set_available_only(self, enabled: bool) -> None:
        value = bool(enabled)
        if value != self._available_only:
            self._available_only = value
            self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        if self._available_only and not bool(index.data(AVAILABLE_ROLE)):
            return False
        search_text = str(index.data(SEARCH_ROLE) or "").casefold()
        return not self._query or self._query in search_text

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        left_value = left.data(SORT_ROLE)
        right_value = right.data(SORT_ROLE)
        if left_value is None:
            return self.sortOrder() == Qt.DescendingOrder and right_value is not None
        if right_value is None:
            return self.sortOrder() != Qt.DescendingOrder
        return left_value < right_value


class JobFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._query = ""
        self._status = ""
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(True)

    def set_query(self, text: str) -> None:
        query = text.strip().casefold()
        if query != self._query:
            self._query = query
            self.invalidateFilter()

    def set_status(self, status: str) -> None:
        normalized = status.strip().casefold()
        if normalized == "all states":
            normalized = ""
        if normalized != self._status:
            self._status = normalized
            self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        status = str(index.data(STATUS_ROLE) or "").casefold()
        search_text = str(index.data(SEARCH_ROLE) or "").casefold()
        return (
            (not self._status or status == self._status)
            and (not self._query or self._query in search_text)
        )

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        left_value = left.data(SORT_ROLE)
        right_value = right.data(SORT_ROLE)
        if left_value is None:
            return self.sortOrder() == Qt.DescendingOrder and right_value is not None
        if right_value is None:
            return self.sortOrder() != Qt.DescendingOrder
        return left_value < right_value
