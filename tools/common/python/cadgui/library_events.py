"""Qt browser input callbacks and stale-item guards."""

from __future__ import annotations

from typing import Optional
from PyQt5.QtWidgets import QTreeWidgetItem, QListWidgetItem
from cadview.catalog import CatalogLibrary, CatalogCell, CatalogView
from .library_sources import _ObjectSelection, _ViewSource
from .library_tree import LibraryTreeWidget
from .library_selection_restore import item_value


class BrowserEvents:
    """Translate Qt input into one browser operation."""

    def __init__(self, selection, *, rebuild, changed, activate, is_rebuilding):
        self.selection = selection
        self.rebuild = rebuild
        self.changed = changed
        self.activate = activate
        self.is_rebuilding = is_rebuilding

    def connect(self, columns):
        columns.library_list.currentItemChanged.connect(self.library_changed)
        columns.category_list.currentItemChanged.connect(self.category_changed)
        columns.cell_list.currentItemChanged.connect(self.cell_changed)
        columns.view_list.currentItemChanged.connect(self.view_changed)
        columns.view_list.itemDoubleClicked.connect(self.activate_view_item)
        for field in (columns.library_filter, columns.category_filter,
                      columns.cell_filter, columns.view_filter):
            field.textChanged.connect(self.filter_changed)

    def filter_changed(self, _text: str) -> None:
        if self.is_rebuilding():
            return
        preferred = self.selection._partial_selection()
        before = self.selection._object_selection()
        self.rebuild(preferred, before=before)

    def library_changed(
        self,
        current: Optional[QTreeWidgetItem],
        _previous: Optional[QTreeWidgetItem],
    ) -> None:
        if self.is_rebuilding():
            return
        old_cell = self.selection.selected_cell
        old_view = self.selection.selected_view
        old_library = item_value(_previous)
        old_source = self.selection._selected_view_source()
        before: _ObjectSelection = (
            (
                old_library
                if isinstance(old_library, CatalogLibrary)
                else old_source.library if old_source is not None else None
            ),
            old_source.cell if old_source is not None else old_cell,
            old_source.view if old_source is not None else old_view,
        )
        library = item_value(current)
        group_key = None
        if current is not None:
            try:
                if current.data(LibraryTreeWidget.GROUP_ROLE):
                    candidate = current.data(LibraryTreeWidget.GROUP_KEY_ROLE)
                    if isinstance(candidate, tuple):
                        group_key = candidate
            except RuntimeError:
                return
        self.rebuild(
            (
                library.name if isinstance(library, CatalogLibrary) else None,
                group_key,
                None,
                None,
                None,
            ),
            before=before,
        )

    def category_changed(
        self,
        current: Optional[QTreeWidgetItem],
        _previous: Optional[QTreeWidgetItem],
    ) -> None:
        if self.is_rebuilding():
            return
        before = self.selection._object_selection()
        self.rebuild(self.selection._partial_selection(), before=before)

    def cell_changed(
        self,
        current: Optional[QListWidgetItem],
        previous: Optional[QListWidgetItem],
    ) -> None:
        if self.is_rebuilding():
            return
        library = self.selection.selected_library
        old_sources = self.selection._cell_sources(previous)
        old_cell = item_value(previous)
        before: _ObjectSelection = (
            library,
            (
                old_cell
                if isinstance(old_cell, CatalogCell)
                else old_sources[0].cell if old_sources else None
            ),
            self.selection.selected_view,
        )
        cell = item_value(current)
        self.rebuild(
            (
                None if library is None else library.name,
                self.selection._selected_group_key(),
                (
                    cell.name
                    if isinstance(cell, CatalogCell)
                    else current.text() if current is not None else None
                ),
                None,
                None,
            ),
            before=before,
        )

    def view_changed(
        self,
        current: Optional[QListWidgetItem],
        previous: Optional[QListWidgetItem],
    ) -> None:
        if self.is_rebuilding():
            return
        library = self.selection.selected_library
        cell = self.selection.selected_cell
        old_view = item_value(previous)
        try:
            candidate_source = (
                None
                if previous is None
                else previous.data(LibraryTreeWidget.VIEW_SOURCE_ROLE)
            )
        except RuntimeError:
            candidate_source = None
        old_source = (
            candidate_source
            if isinstance(candidate_source, _ViewSource)
            else None
        )
        before: _ObjectSelection = (
            (
                library
                if library is not None
                else old_source.library if old_source is not None else None
            ),
            old_source.cell if old_source is not None else cell,
            (
                old_source.view
                if old_source is not None
                else old_view if isinstance(old_view, CatalogView) else None
            ),
        )
        self.changed(before)

    def activate_view_item(
        self,
        item: Optional[QListWidgetItem],
    ) -> None:
        # A host subscriber can synchronously replace the catalog between the
        # view selection and double-click signals.  Mirror the library-tree
        # stale-item protection so a deleted Qt wrapper never escapes a slot.
        if item is None:
            return
        try:
            if self.selection.view_list.row(item) < 0:
                return
            if item is not self.selection.view_list.currentItem():
                self.selection.view_list.setCurrentItem(item)
        except RuntimeError:
            return
        self.activate()
