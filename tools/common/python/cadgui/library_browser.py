"""Reusable catalog browser composition for host applications.

Presentation only: no cds.lib loading, EDA startup or OA writes. Tree classes
and selection types are same-object compatibility exports. Remove the old
import paths once supported hosts import those types from their owning modules."""

from __future__ import annotations

from typing import Optional
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QWidget, QVBoxLayout
from cadview.catalog import Catalog
from .library_tree import LibraryTreeWidget, LibraryTreeItem
from .category_tree import CategoryTreeWidget, CategoryTreeItem
from .library_categories import (
    CategoryIdentity,
    _CATEGORY_ALL,
    _CATEGORY_UNCATEGORIZED,
    _CategoryNode,
    _MutableCategoryNode,
    _category_legacy_name,
)
from .library_groups import _LibraryLeaf, _LibraryGroup, _LibraryNode
from .library_sources import (
    CatalogSelection,
    _PartialSelection,
    _ObjectSelection,
    _CellSource,
    _ViewSource,
)
from .library_columns import BrowserColumns
from .library_session import BrowserSession


class LibraryBrowserWidget(QWidget):
    """Display a catalog as ``Library | [Category] | Cell | View``.

    Each column has a case-insensitive substring filter.  Selecting a library
    repopulates the cell column and selecting a cell repopulates the view
    column.  Replacing the catalog preserves the qualified selection whenever
    the same visible ``library/cell/view`` still exists.

    The ``*Changed`` signals carry the corresponding catalog object, or
    ``None`` when that level has no selection.  ``selectionChanged`` carries a
    complete three-name tuple, or ``None``.  Double-clicking a view emits
    ``viewActivated`` with three strings; the widget intentionally provides no
    built-in context menu or OA operation.
    """

    libraryChanged = pyqtSignal(object)
    cellChanged = pyqtSignal(object)
    viewChanged = pyqtSignal(object)
    selectionChanged = pyqtSignal(object)
    viewActivated = pyqtSignal(str, str, str)

    def __init__(self, catalog: Optional[Catalog] = None, parent=None, *, content_top_margin=0):
        super().__init__(parent)
        self.setObjectName("libraryBrowser")
        self._columns = BrowserColumns(self, content_top_margin=content_top_margin)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._columns)
        self._session = BrowserSession(self._columns)
        self._session.libraryChanged.connect(self.libraryChanged)
        self._session.cellChanged.connect(self.cellChanged)
        self._session.viewChanged.connect(self.viewChanged)
        self._session.selectionChanged.connect(self.selectionChanged)
        self._session.viewActivated.connect(self.viewActivated)
        self.show_categories.toggled.connect(self.set_show_categories)
        if catalog is not None:
            self.set_catalog(catalog)

    @property
    def splitter(self):
        return self._columns.splitter

    @property
    def library_filter(self):
        return self._columns.library_filter

    @property
    def category_filter(self):
        return self._columns.category_filter

    @property
    def cell_filter(self):
        return self._columns.cell_filter

    @property
    def view_filter(self):
        return self._columns.view_filter

    @property
    def library_list(self):
        return self._columns.library_list

    @property
    def category_list(self):
        return self._columns.category_list

    @property
    def cell_list(self):
        return self._columns.cell_list

    @property
    def view_list(self):
        return self._columns.view_list

    @property
    def show_categories(self):
        return self._columns.show_categories

    @property
    def categories_visible(self):
        return self._columns.categories_visible

    @property
    def selected_library(self):
        return self._session.current.selected_library

    @property
    def selected_combined_library(self):
        return self._session.current.selected_combined_library

    @property
    def selected_cell(self):
        return self._session.current.selected_cell

    @property
    def selected_view(self):
        return self._session.current.selected_view

    @property
    def selection(self):
        return self._session.current.selection

    @property
    def selected_category_identity(self):
        return self._session.current.selected_category_identity

    @property
    def selected_category(self) -> Optional[str]:
        """Return the legacy display name/path for the active category.

        ``selected_category_identity`` is the collision-free API.  This
        string-valued property remains for existing MTS callers.
        """

        identity = self.selected_category_identity
        if identity is None:
            return None
        return _category_legacy_name(identity)

    @property
    def catalog(self):
        return self._session.catalog

    def set_show_categories(self, enabled):
        enabled = bool(enabled)
        if self.show_categories.isChecked() != enabled:
            # The checkbox is the sole visibility value; its signal runs the
            # same update path used for a user toggle.
            self.show_categories.setChecked(enabled)
            return
        if not self._columns.set_categories_visible(enabled):
            return
        self._session.refresh()

    def set_catalog(self, catalog, *, preserve_selection=True):
        self._session.set_catalog(catalog, preserve_selection=preserve_selection)

    def clear(self):
        self._session.clear()

    def clear_selection(self):
        self._session.clear_selection()

    def select(self, library, cell, view):
        return self._session.select(library, cell, view)

    def activate_current_view(self):
        return self._session.activate_current_view()

    # Legacy host hooks. Remove after supported hosts use tree.toggle_group
    # and view activation signals; keep no second callback implementation.
    def _library_item_pressed(self, item, _column=0):
        self.library_list.toggle_group(item)

    def _activate_view_item(self, item):
        self._session.events.activate_view_item(item)

    showCategories = set_show_categories
    setCatalog = set_catalog
    clearSelection = clear_selection
    activateCurrentView = activate_current_view


__all__ = ["CatalogSelection", "CategoryIdentity", "CategoryTreeWidget",
           "LibraryTreeWidget", "LibraryBrowserWidget"]
