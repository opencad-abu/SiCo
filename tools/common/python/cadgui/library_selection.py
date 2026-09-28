"""Live Qt selection adapter retaining exact physical source identity."""

from __future__ import annotations

from typing import Optional, Tuple
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QListWidgetItem, QTreeWidgetItem
from cadview.catalog import CatalogLibrary, CatalogCell, CatalogView
from .library_tree import LibraryTreeWidget
from .library_categories import CategoryIdentity, _is_category_identity
from .library_sources import CatalogSelection, _PartialSelection, _ObjectSelection, _CellSource, _ViewSource
from .library_selection_restore import item_value


class BrowserSelection:
    """Read the current physical selection from the four Qt columns."""

    def __init__(self, library_list, category_list, cell_list, view_list):
        self.library_list = library_list
        self.category_list = category_list
        self.cell_list = cell_list
        self.view_list = view_list

    @property
    def selected_library(self) -> Optional[CatalogLibrary]:
        """Return the selected physical or top-level combined library."""

        value = item_value(self.library_list.currentItem())
        return value if isinstance(value, CatalogLibrary) else None

    @property
    def selected_combined_library(self) -> Optional[str]:
        """Return the selected COMBINE group name, or ``None``."""

        item = self.library_list.currentItem()
        try:
            if item is None or not item.data(LibraryTreeWidget.GROUP_ROLE):
                return None
            name = item.data(LibraryTreeWidget.NODE_NAME_ROLE)
        except RuntimeError:
            return None
        return name if isinstance(name, str) else None

    @property
    def selected_cell(self) -> Optional[CatalogCell]:
        """Return the selected cell object, if any."""

        value = item_value(self.cell_list.currentItem())
        if isinstance(value, CatalogCell):
            return value
        if isinstance(value, tuple) and value:
            first = value[0]
            if isinstance(first, _CellSource):
                return first.cell
        return None

    @property
    def selected_view(self) -> Optional[CatalogView]:
        """Return the selected view object, if any."""

        source = self._selected_view_source()
        if source is not None:
            return source.view
        value = item_value(self.view_list.currentItem())
        return value if isinstance(value, CatalogView) else None

    @property
    def selection(self) -> Optional[CatalogSelection]:
        """Return the complete selected ``(library, cell, view)`` names."""

        source = self._selected_view_source()
        if source is not None:
            return (source.library.name, source.cell.name, source.view.name)
        library = self.selected_library
        cell = self.selected_cell
        view = self.selected_view
        if library is None or cell is None or view is None:
            return None
        return (library.name, cell.name, view.name)

    def _selected_view_source(self) -> Optional[_ViewSource]:
        item = self.view_list.currentItem()
        if item is None:
            return None
        try:
            value = item.data(LibraryTreeWidget.VIEW_SOURCE_ROLE)
        except RuntimeError:
            return None
        return value if isinstance(value, _ViewSource) else None

    @staticmethod
    def _cell_sources(item: Optional[QListWidgetItem]) -> Tuple[_CellSource, ...]:
        if item is None:
            return ()
        try:
            value = item.data(LibraryTreeWidget.CELL_SOURCES_ROLE)
        except RuntimeError:
            return ()
        if isinstance(value, tuple) and all(
            isinstance(candidate, _CellSource) for candidate in value
        ):
            return value
        return ()

    def _selected_cell_sources(self) -> Tuple[_CellSource, ...]:
        return self._cell_sources(self.cell_list.currentItem())

    def _selected_library_item(self) -> Optional[QTreeWidgetItem]:
        item = self.library_list.currentItem()
        if item is None:
            return None
        try:
            return item if item.treeWidget() is self.library_list else None
        except RuntimeError:
            return None

    def _selected_libraries(self) -> Tuple[CatalogLibrary, ...]:
        item = self._selected_library_item()
        if item is None:
            return ()
        try:
            is_group = bool(item.data(LibraryTreeWidget.GROUP_ROLE))
            libraries = (
                item.data(LibraryTreeWidget.GROUP_LIBRARIES_ROLE)
                if is_group
                else ()
            )
        except RuntimeError:
            return ()
        if is_group and isinstance(libraries, tuple):
            return tuple(
                candidate
                for candidate in libraries
                if isinstance(candidate, CatalogLibrary)
            )
        value = item_value(item)
        if isinstance(value, CatalogLibrary):
            return (value,)
        return ()

    def _partial_selection(self) -> _PartialSelection:
        library = self.selected_library
        cell = self.selected_cell
        view = self.selected_view
        source = self._selected_view_source()
        return (
            None if library is None else library.name,
            self._selected_group_key(),
            None if cell is None else cell.name,
            None if view is None else view.name,
            None if source is None else source.library.name,
        )

    def _selected_group_key(self) -> Optional[Tuple[str, ...]]:
        item = self._selected_library_item()
        if item is None:
            return None
        try:
            if not item.data(LibraryTreeWidget.GROUP_ROLE):
                return None
            key = item.data(LibraryTreeWidget.GROUP_KEY_ROLE)
        except RuntimeError:
            return None
        return key if isinstance(key, tuple) else None

    def _object_selection(self) -> _ObjectSelection:
        library = self.selected_library
        cell = self.selected_cell
        view = self.selected_view
        source = self._selected_view_source()
        if library is None and source is not None:
            # Custom/legacy catalogs can describe a COMBINE group without a
            # physical top CatalogLibrary.  Use the selected physical view as
            # the change identity so selectionChanged still fires correctly.
            return (source.library, source.cell, source.view)
        return (library, cell, view)

    @property
    def selected_category_identity(self) -> Optional[CategoryIdentity]:
        """Return ``(kind, path)`` without virtual/real-name collisions."""

        value = item_value(self.category_list.currentItem())
        return value if _is_category_identity(value) else None
