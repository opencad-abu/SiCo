"""COMBINE tree presentation and branch fold ownership."""

from __future__ import annotations

from typing import Optional, Tuple
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QAbstractItemView
from cadview.catalog import CatalogLibrary
from .tree_item import LibraryTreeItem
from .tree_list import ListCompatibleTree


class LibraryTreeWidget(ListCompatibleTree):
    """Native COMBINE tree with the historical ``QListWidget`` facade.

    ``count()/item()/setCurrentRow()/currentRow()`` intentionally address
    unique physical libraries because that is the API consumed by MTS.  The
    tree-aware ``all_*`` methods expose every group/member occurrence in
    preorder, including repeated members under separate COMBINE branches.
    """

    GROUP_ROLE = Qt.UserRole + 1
    COLLAPSED_ROLE = Qt.UserRole + 2
    CHILD_ROLE = Qt.UserRole + 3
    NODE_NAME_ROLE = Qt.UserRole + 4
    ANCESTORS_ROLE = Qt.UserRole + 5
    DEPTH_ROLE = Qt.UserRole + 6
    GROUP_KEY_ROLE = Qt.UserRole + 7
    GROUP_LIBRARIES_ROLE = Qt.UserRole + 8
    CELL_SOURCES_ROLE = Qt.UserRole + 9
    VIEW_SOURCE_ROLE = Qt.UserRole + 10

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(1)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self._library_items: list[LibraryTreeItem] = []
        # Tree branches start folded.  Store the exceptional expanded paths
        # so a rebuild can preserve an explicit user expansion without making
        # newly discovered COMBINE groups open by default.
        self._expanded_groups: set[Tuple[str, ...]] = set()
        self._filter_active = False
        self._groups_by_key: dict[Tuple[str, ...], LibraryTreeItem] = {}
        self.itemExpanded.connect(self._group_expanded)
        self.itemCollapsed.connect(self._group_collapsed)

    def _row_items(self):
        return self._library_items

    def clear(self) -> None:
        self._library_items = []
        self._groups_by_key = {}
        super().clear()

    def reset_fold_state(self) -> None:
        """Forget folds that belong to a previously loaded source catalog."""

        self._expanded_groups.clear()

    def set_filter_active(self, active: bool) -> None:
        """Temporarily reveal filtered matches without losing fold state."""

        self._filter_active = bool(active)
        for item in self._flat_items():
            if item.data(self.GROUP_ROLE):
                key = item.data(self.GROUP_KEY_ROLE)
                if self._filter_active:
                    item.setExpanded(True)
                else:
                    item.setExpanded(key in self._expanded_groups)

    def addItem(self, item: LibraryTreeItem) -> None:  # noqa: N802
        """Append a root item using the QListWidget-compatible name."""

        self.addTopLevelItem(item)
        self._reindex_items()

    def takeItem(self, row: int):  # noqa: N802
        """Remove a flattened tree item by preorder row."""

        item = self.all_item(row)
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
            self._reindex_items()
        return removed

    def _reindex_items(self) -> None:
        """Rebuild compatibility indexes after a generic tree mutation."""

        self._library_items = []
        self._groups_by_key = {}
        seen_libraries: set[str] = set()
        for item in self._flat_items():
            if bool(item.data(self.GROUP_ROLE)):
                key = item.data(self.GROUP_KEY_ROLE)
                if isinstance(key, tuple):
                    self._groups_by_key[key] = item
                continue
            library = item.data(Qt.UserRole)
            name = getattr(library, "name", None)
            if isinstance(name, str) and name not in seen_libraries:
                seen_libraries.add(name)
                self._library_items.append(item)

    def _group_expanded(self, item: Optional[LibraryTreeItem]) -> None:
        if self._filter_active or item is None:
            return
        try:
            key = item.data(self.GROUP_KEY_ROLE)
        except RuntimeError:
            return
        if isinstance(key, tuple):
            self._expanded_groups.add(key)
        item.setData(self.COLLAPSED_ROLE, False)

    def _group_collapsed(self, item: Optional[LibraryTreeItem]) -> None:
        if self._filter_active or item is None:
            return
        try:
            key = item.data(self.GROUP_KEY_ROLE)
        except RuntimeError:
            return
        if isinstance(key, tuple):
            self._expanded_groups.discard(key)
        item.setData(self.COLLAPSED_ROLE, True)

    def add_group_row(
        self,
        name: str,
        *,
        depth: int = 0,
        ancestors: Tuple[str, ...] = (),
        libraries: Tuple[CatalogLibrary, ...] = (),
    ) -> LibraryTreeItem:
        """Append one virtual COMBINE row and return it."""

        group_key = (*ancestors, name)
        expanded = group_key in self._expanded_groups
        group = LibraryTreeItem([name])
        physical_top = next(
            (library for library in libraries if library.name == name), None
        )
        group.setData(Qt.UserRole, physical_top)
        group.setData(self.GROUP_ROLE, True)
        group.setData(self.COLLAPSED_ROLE, not expanded)
        group.setData(self.CHILD_ROLE, depth > 0)
        group.setData(self.NODE_NAME_ROLE, name)
        group.setData(self.ANCESTORS_ROLE, tuple(ancestors))
        group.setData(self.DEPTH_ROLE, int(depth))
        group.setData(self.GROUP_KEY_ROLE, group_key)
        group.setData(self.GROUP_LIBRARIES_ROLE, tuple(libraries))
        group.setToolTip(f"{name} [COMBINED]")
        self._groups_by_key[group_key] = group
        parent = self._groups_by_key.get(tuple(ancestors))
        if parent is None:
            self.addTopLevelItem(group)
        else:
            parent.addChild(group)
        group.setExpanded(expanded or self._filter_active)
        return group

    def add_library_row(
        self,
        library: CatalogLibrary,
        *,
        depth: int = 0,
        ancestors: Tuple[str, ...] = (),
    ) -> LibraryTreeItem:
        """Append one selectable physical library row and return it."""

        item = LibraryTreeItem([library.name])
        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        item.setData(Qt.UserRole, library)
        item.setData(self.GROUP_ROLE, False)
        item.setData(self.CHILD_ROLE, depth > 0)
        item.setData(self.NODE_NAME_ROLE, library.name)
        item.setData(self.ANCESTORS_ROLE, tuple(ancestors))
        item.setData(self.DEPTH_ROLE, int(depth))
        parent = self._groups_by_key.get(tuple(ancestors))
        if parent is None:
            self.addTopLevelItem(item)
        else:
            parent.addChild(item)
        # A physical library is allowed in multiple combined libraries.  The
        # flat tree displays every occurrence, while the historical
        # count()/item()/setCurrentRow() compatibility surface addresses the
        # first occurrence of each physical library exactly once.
        if not any(
            getattr(existing.data(Qt.UserRole), "name", None) == library.name
            for existing in self._library_items
        ):
            self._library_items.append(item)
        return item

    def toggle_group(self, item: Optional[LibraryTreeItem]) -> bool:
        """Toggle a live COMBINE row and ignore stale Qt signal payloads.

        Older hosts may retain an item while a selection callback
        synchronously rebuilds and clears the tree.  A later compatibility
        callback can therefore receive ``None`` or a deleted C++ wrapper.
        Treat that as a normal no-op instead of letting an exception escape a
        Qt slot and abort the GUI process.
        """

        if item is None:
            return False
        try:
            if item.treeWidget() is not self or not item.data(self.GROUP_ROLE):
                return False
            group_key = item.data(self.GROUP_KEY_ROLE)
        except RuntimeError:
            # PyQt raises when a queued/succeeding callback still carries the
            # Python wrapper for an item deleted by QTreeWidget.clear().
            return False
        if not (
            isinstance(group_key, tuple)
            and group_key
            and all(isinstance(name, str) for name in group_key)
        ):
            return False
        item.setExpanded(not item.isExpanded())
        return True

    def select_preferred(
        self,
        preferred: Optional[str],
        preferred_group: Optional[Tuple[str, ...]] = None,
    ) -> bool:
        selected = None
        if preferred_group is not None:
            selected = self._groups_by_key.get(preferred_group)
        if selected is None and preferred is not None:
            for candidate in self._library_items:
                value = candidate.data(Qt.UserRole)
                if getattr(value, "name", None) == preferred:
                    selected = candidate
                    break
        if selected is not None:
            self.setCurrentItem(selected)
            return True
        if self.topLevelItemCount():
            # Native Library Manager behavior selects the first displayed
            # root.  For a COMBINE tree that is the aggregate group itself,
            # which keeps its default fold while showing composite cells.
            self.setCurrentItem(self.topLevelItem(0))
        else:
            self.setCurrentRow(-1)
        return False
