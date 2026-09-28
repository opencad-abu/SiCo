"""Row compatibility for native Qt trees."""

from __future__ import annotations

from typing import Optional
from PyQt5.QtWidgets import QTreeWidget
from .tree_item import LibraryTreeItem


class ListCompatibleTree(QTreeWidget):
    """Native tree with a small row facade for existing hosts."""

    def _row_items(self):
        return self._flat_items()

    def _flat_items(self) -> list[LibraryTreeItem]:
        result: list[LibraryTreeItem] = []

        def visit(item: LibraryTreeItem) -> None:
            result.append(item)
            for child_index in range(item.childCount()):
                visit(item.child(child_index))

        for index in range(self.topLevelItemCount()):
            visit(self.topLevelItem(index))
        return result

    def all_count(self) -> int:
        return len(self._flat_items())

    def all_item(self, row: int) -> Optional[LibraryTreeItem]:
        try:
            index = int(row)
        except (TypeError, ValueError):
            return None
        items = self._flat_items()
        if index < 0 or index >= len(items):
            return None
        return items[index]

    def count(self) -> int:  # noqa: D401 - mirrors QListWidget.count
        """Return the number of compatibility rows."""

        return len(self._row_items())

    def item(self, row: int) -> Optional[LibraryTreeItem]:
        """Return an item by compatibility row number."""

        try:
            index = int(row)
        except (TypeError, ValueError):
            return None
        if index < 0 or index >= len(self._row_items()):
            return None
        return self._row_items()[index]

    def visible_item(self, row: int) -> Optional[LibraryTreeItem]:
        """Return a visible row, useful to tree-aware callers."""

        visible = [item for item in self._row_items() if not item.isHidden()]
        try:
            index = int(row)
        except (TypeError, ValueError):
            return None
        if index < 0 or index >= len(visible):
            return None
        return visible[index]

    def setCurrentRow(self, row: int) -> None:  # noqa: N802
        """Select a compatibility row by compatibility row number."""

        try:
            index = int(row)
        except (TypeError, ValueError):
            self.setCurrentItem(None)
            self.clearSelection()
            return
        if index < 0 or index >= len(self._row_items()):
            self.setCurrentItem(None)
            self.clearSelection()
            return
        self.setCurrentItem(self._row_items()[index])

    def currentRow(self) -> int:  # noqa: N802
        """Return the compatibility row for currentItem."""

        current = self.currentItem()
        try:
            return self._row_items().index(current)
        except ValueError:
            return -1
