"""Catalog replacement, selection rebuilds and browser change notifications."""

from __future__ import annotations

from typing import Optional
from PyQt5.QtCore import QObject, pyqtSignal
from cadview.catalog import Catalog, CatalogLibrary
from .library_sources import CatalogSelection, _PartialSelection, _ObjectSelection, _CellSource, _selection_identities
from .library_selection import BrowserSelection
from .library_selection_restore import select_cell, select_view
from .library_events import BrowserEvents
from .library_render import populate_libraries, populate_categories, populate_cells, populate_views
from .library_categories import cell_matches_category
from .library_filter import matches
from .library_tree import LibraryTreeWidget


class BrowserSession(QObject):
    """Own the presented catalog and serialize rebuild/change operations."""

    libraryChanged = pyqtSignal(object)
    cellChanged = pyqtSignal(object)
    viewChanged = pyqtSignal(object)
    selectionChanged = pyqtSignal(object)
    viewActivated = pyqtSignal(str, str, str)

    def __init__(self, columns):
        super().__init__(columns)
        self.columns = columns
        self._catalog = None
        self._rebuilding = False
        self._last_emitted_selection = None
        self.current = BrowserSelection(columns.library_list, columns.category_list,
                                        columns.cell_list, columns.view_list)
        self.events = BrowserEvents(self.current, rebuild=self._rebuild,
                                    changed=self._emit_changes,
                                    activate=self.activate_current_view,
                                    is_rebuilding=lambda: self._rebuilding)
        self.events.connect(columns)

    @property
    def catalog(self) -> Optional[Catalog]:
        """Return the catalog currently presented by the widget."""

        return self._catalog

    def set_catalog(
        self,
        catalog: Optional[Catalog],
        *,
        preserve_selection: bool = True,
    ) -> None:
        """Present ``catalog`` and optionally preserve the qualified selection.

        ``None`` is accepted so asynchronous consumers can invalidate stale
        content before a new catalog arrives.  Filters are retained; a previous
        selection excluded by a current filter cannot be restored and the first
        visible item at that level is selected instead.
        """

        if catalog is not None and not isinstance(catalog, Catalog):
            raise TypeError("catalog must be a cadview.catalog.Catalog or None")
        previous = (
            self.current._partial_selection()
            if preserve_selection
            else (None, None, None, None, None)
        )
        before = self.current._object_selection()
        previous_source = self._catalog_source_identity(self._catalog)
        next_source = self._catalog_source_identity(catalog)
        if previous_source != next_source:
            # Fold keys are COMBINE name paths.  The same path can legitimately
            # occur in unrelated projects, so carrying it across cds.lib files
            # leaks UI state between Process tabs.  Ordinary refreshes of the
            # same source still preserve folds through list rebuilds.
            self.columns.library_list.reset_fold_state()
        self._catalog = catalog
        self._rebuild(previous, before=before)

    @staticmethod
    def _catalog_source_identity(catalog: Optional[Catalog]):
        if catalog is None:
            return None
        return str(catalog.cds_library_file)

    def _category_context(self, library: Optional[CatalogLibrary]):
        combined = self.current.selected_combined_library
        if combined is not None:
            item = self.columns.library_list.currentItem()
            try:
                key = item.data(LibraryTreeWidget.GROUP_KEY_ROLE)
                libraries = item.data(LibraryTreeWidget.GROUP_LIBRARIES_ROLE)
            except (AttributeError, RuntimeError):
                key = (combined,)
                libraries = ()
            return (
                self._catalog_source_identity(self._catalog),
                "combined",
                tuple(key) if isinstance(key, tuple) else (combined,),
                tuple(
                    (candidate.name, str(candidate.path))
                    for candidate in libraries
                    if isinstance(candidate, CatalogLibrary)
                ),
            )
        if library is None:
            return None
        return (
            self._catalog_source_identity(self._catalog),
            library.name,
            str(library.path),
        )

    def clear(self) -> None:
        """Remove the catalog and all selections while retaining filter text."""

        self.set_catalog(None, preserve_selection=False)

    def clear_selection(self) -> None:
        """Clear the current selection without removing the catalog."""

        before = self.current._object_selection()
        with self.columns.blocked_lists():
            self.columns.library_list.clearSelection()
            self.columns.library_list.setCurrentRow(-1)
            self.columns.category_list.clear()
            # Cells and views belong to the previously selected parents.  Do
            # not leave those stale rows interactive after their parent
            # selections have been cleared.
            self.columns.cell_list.clear()
            self.columns.view_list.clear()
        self._emit_changes(before)

    def select(self, library: str, cell: str, view: str) -> bool:
        """Select an exact visible ``library/cell/view`` path.

        The operation is non-destructive on failure: it returns ``False`` and
        keeps the current selection when the path does not exist or is excluded
        by one of the active filters.
        """

        target = (str(library), str(cell), str(view))
        if not self._selection_is_visible(target):
            return False
        before = self.current._object_selection()
        self._rebuild((target[0], None, target[1], target[2], target[0]), before=before)
        return self.current.selection == target

    def activate_current_view(self) -> bool:
        """Emit ``viewActivated`` for the current complete selection."""

        selected = self.current.selection
        if selected is None:
            return False
        self.viewActivated.emit(*selected)
        return True

    def _emit_changes(self, before: _ObjectSelection) -> None:
        after = self.current._object_selection()
        old_ids = _selection_identities(before)
        new_ids = _selection_identities(after)
        exact_selection = self.current.selection
        if old_ids[0] != new_ids[0]:
            self.libraryChanged.emit(after[0])
        if old_ids[1] != new_ids[1]:
            self.cellChanged.emit(after[1])
        if old_ids[2] != new_ids[2]:
            self.viewChanged.emit(after[2])
        if (
            old_ids != new_ids
            or self._last_emitted_selection != exact_selection
        ):
            self.selectionChanged.emit(exact_selection)
        self._last_emitted_selection = exact_selection

    def _selection_is_visible(self, selection: CatalogSelection) -> bool:
        if self._catalog is None:
            return False
        library_name, cell_name, view_name = selection
        if not matches(library_name, self.columns.library_filter.text()):
            return False
        library = self._catalog.library(library_name)
        if library is None or not matches(cell_name, self.columns.cell_filter.text()):
            return False
        cell = next((item for item in library.cells if item.name == cell_name), None)
        if cell is None or not cell_matches_category(library, cell, self.current.selected_category_identity, self.columns.categories_visible):
            return False
        if not matches(view_name, self.columns.view_filter.text()):
            return False
        return any(item.name == view_name for item in cell.views)

    def refresh(self):
        self._rebuild(self.current._partial_selection(), before=self.current._object_selection())

    def _rebuild(self, preferred: _PartialSelection, *, before=None):
        old = self.current._object_selection() if before is None else before
        library_name, group_key, cell_name, view_name, view_library_name = preferred
        category = self.current.selected_category_identity
        columns, selected = self.columns, self.current
        self._rebuilding = True
        try:
            with columns.blocked_lists():
                columns.clear_rows()
                populate_libraries(columns.library_list, self._catalog, columns.library_filter.text())
                library_preserved = columns.library_list.select_preferred(library_name, group_key)
                libraries = selected._selected_libraries()
                columns.category_list.set_context(self._category_context(selected.selected_library))
                if libraries:
                    if columns.categories_visible:
                        populate_categories(columns.category_list, libraries,
                                            columns.category_filter.text(), category)
                    populate_cells(columns.cell_list, libraries, selected.selected_category_identity,
                                   columns.categories_visible, columns.cell_filter.text(),
                                   selected.selected_combined_library)
                cell_preserved = select_cell(columns.cell_list, cell_name if library_preserved else None)
                if selected.selected_cell is not None:
                    sources = selected._selected_cell_sources()
                    if not sources and selected.selected_library is not None:
                        sources = (_CellSource(selected.selected_library, selected.selected_cell),)
                    populate_views(columns.view_list, sources, columns.view_filter.text())
                select_view(columns.view_list,
                            view_name if library_preserved and cell_preserved else None,
                            view_library_name)
        finally:
            self._rebuilding = False
        self._emit_changes(old)
