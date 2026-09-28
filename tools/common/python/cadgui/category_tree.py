"""Category tree presentation and context-scoped fold ownership."""

from __future__ import annotations

from typing import Optional
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QAbstractItemView
from .tree_item import LibraryTreeItem
from .tree_list import ListCompatibleTree
from .library_categories import CategoryIdentity


class CategoryTreeItem(LibraryTreeItem):
    """One selectable Category node in the native Category tree."""


class CategoryTreeWidget(ListCompatibleTree):
    """Native Category hierarchy with a small list-compatible facade.

    Unlike :class:`LibraryTreeWidget`, ``count()`` and ``item()`` expose all
    Category nodes in depth-first display order.  This preserves the row-based
    API used by existing hosts while allowing callers to use native
    ``topLevelItem()/child()`` tree traversal as well.
    """

    IDENTITY_ROLE = Qt.UserRole
    DIRECT_MATCH_ROLE = Qt.UserRole + 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(1)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self._items: list[CategoryTreeItem] = []
        self._items_by_identity: dict[CategoryIdentity, CategoryTreeItem] = {}
        # Like COMBINE groups, real Category parents start folded.  Persist
        # only paths that the user explicitly expanded in this library
        # context; virtual/leaf rows are unaffected.
        self._expanded_categories: set[CategoryIdentity] = set()
        self._filter_active = False
        self._context = None
        self.itemExpanded.connect(self._category_expanded)
        self.itemCollapsed.connect(self._category_collapsed)

    def _row_items(self):
        return self._items

    @property
    def filter_active(self) -> bool:
        return self._filter_active

    def clear(self) -> None:
        self._items = []
        self._items_by_identity = {}
        super().clear()

    def set_context(self, context) -> None:
        """Scope remembered folds to one source catalog and physical library."""

        if context == self._context:
            return
        self._context = context
        self._expanded_categories.clear()

    def reset_fold_state(self) -> None:
        self._expanded_categories.clear()

    def set_filter_active(self, active: bool) -> None:
        """Reveal filtered branches temporarily and restore user folds later."""

        self._filter_active = bool(active)
        for item in self._items:
            identity = item.data(self.IDENTITY_ROLE)
            if not (
                isinstance(identity, tuple)
                and len(identity) == 2
                and identity[0] == "category"
            ):
                continue
            if self._filter_active:
                item.setExpanded(True)
            else:
                item.setExpanded(identity in self._expanded_categories)

    def addItem(self, item: CategoryTreeItem) -> None:  # noqa: N802
        self.addTopLevelItem(item)

        def register(candidate: CategoryTreeItem) -> None:
            self._register_item(candidate)
            for child_index in range(candidate.childCount()):
                register(candidate.child(child_index))

        register(item)

    def takeItem(self, row: int):  # noqa: N802
        item = self.item(row)
        if item is None:
            return None
        parent = item.parent()
        if parent is None:
            index = self.indexOfTopLevelItem(item)
            removed = self.takeTopLevelItem(index) if index >= 0 else None
        else:
            index = parent.indexOfChild(item)
            removed = parent.takeChild(index) if index >= 0 else None
        if removed is not None:
            self._items = self._flat_items()
            self._items_by_identity = {
                candidate.data(self.IDENTITY_ROLE): candidate
                for candidate in self._items
            }
        return removed

    def add_category_row(
        self,
        identity: CategoryIdentity,
        display: str,
        *,
        parent: Optional[CategoryTreeItem] = None,
        directly_matched: bool = True,
        tooltip: str = "",
    ) -> CategoryTreeItem:
        item = CategoryTreeItem([display])
        item.setData(self.IDENTITY_ROLE, identity)
        item.setData(self.DIRECT_MATCH_ROLE, bool(directly_matched))
        if tooltip:
            item.setToolTip(tooltip)
        if parent is None:
            self.addTopLevelItem(item)
        else:
            parent.addChild(item)
        self._register_item(item)
        if identity[0] == "category":
            item.setExpanded(
                self._filter_active
                or identity in self._expanded_categories
            )
        return item

    def item_for_identity(
        self, identity: CategoryIdentity
    ) -> Optional[CategoryTreeItem]:
        return self._items_by_identity.get(identity)

    def first_direct_match(self) -> Optional[CategoryTreeItem]:
        return next(
            (
                item
                for item in self._items
                if bool(item.data(self.DIRECT_MATCH_ROLE))
            ),
            None,
        )

    def _register_item(self, item: CategoryTreeItem) -> None:
        self._items.append(item)
        identity = item.data(self.IDENTITY_ROLE)
        if isinstance(identity, tuple):
            self._items_by_identity.setdefault(identity, item)

    def _category_expanded(
        self, item: Optional[CategoryTreeItem]
    ) -> None:
        if self._filter_active or item is None:
            return
        try:
            identity = item.data(self.IDENTITY_ROLE)
        except RuntimeError:
            return
        if (
            isinstance(identity, tuple)
            and len(identity) == 2
            and identity[0] == "category"
        ):
            self._expanded_categories.add(identity)

    def _category_collapsed(
        self, item: Optional[CategoryTreeItem]
    ) -> None:
        if self._filter_active or item is None:
            return
        try:
            identity = item.data(self.IDENTITY_ROLE)
        except RuntimeError:
            return
        if (
            isinstance(identity, tuple)
            and len(identity) == 2
            and identity[0] == "category"
        ):
            self._expanded_categories.discard(identity)

    def select_preferred(
        self, preferred: Optional[CategoryIdentity]
    ) -> bool:
        selected = (
            self.item_for_identity(preferred)
            if preferred is not None
            else None
        )
        if (
            selected is not None
            and self.filter_active
            and not bool(
                selected.data(CategoryTreeWidget.DIRECT_MATCH_ROLE)
            )
        ):
            # Ancestors retained only to expose a matching descendant are not
            # themselves filter results.  Prefer the actual matching node so
            # filtering does not silently apply a broader parent Category.
            selected = None
        if selected is not None:
            self.setCurrentItem(selected)
            return True
        fallback = self.first_direct_match()
        if fallback is not None:
            self.setCurrentItem(fallback)
            return False
        self.setCurrentRow(-1)
        return False
