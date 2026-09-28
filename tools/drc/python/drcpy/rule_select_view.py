"""Literal tree filtering and deterministic wheel navigation."""

from __future__ import annotations

from .rule_select_model import NAME_ROLE
from .rule_select_qt import QModelIndex, QSortFilterProxyModel, Qt, QTreeView, QWheelEvent, scroll_item_view


class RuleSelectFilterProxyModel(QSortFilterProxyModel):
    """Literal case-insensitive recursive filtering without rebuilding rows."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._query = ""
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setRecursiveFilteringEnabled(True)
        self.setDynamicSortFilter(False)

    def setFilterFixedString(self, text: str) -> None:
        self.set_query(text)

    def set_query(self, text: str) -> None:
        query = text.strip().casefold()
        if query != self._query:
            self._query = query
            self.invalidateFilter()

    def _index_matches(self, index: QModelIndex) -> bool:
        name = str(index.data(NAME_ROLE) or index.data(Qt.DisplayRole) or "")
        return self._query in name.casefold()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if not self._query:
            return True
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        if self._index_matches(index):
            return True
        if source_parent.isValid() and self._index_matches(source_parent):
            return True
        for child_row in range(model.rowCount(index)):
            if self._index_matches(model.index(child_row, 0, index)):
                return True
        return False


class RuleTreeView(QTreeView):
    """Tree view with deterministic wheel and touchpad scrolling."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setVerticalScrollMode(QTreeView.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    def scroll_wheel_event(self, event: QWheelEvent) -> bool:
        return scroll_item_view(self, event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not self.scroll_wheel_event(event):
            super().wheelEvent(event)
