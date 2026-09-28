"""Qt layout for Library, Category, Cell and View columns."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QSplitter, QLineEdit, QAbstractItemView,
    QCheckBox, QLabel, QListWidget,
)
from .library_tree import LibraryTreeWidget
from .category_tree import CategoryTreeWidget


class BrowserColumns(QWidget):
    """Arrange the browser columns and optional category pane."""

    def __init__(self, parent=None, *, content_top_margin=0):
        super().__init__(parent)
        self._equalize_categories_on_show = False
        # The checkbox is an input control.  Keep the applied pane state here
        # so calling set_show_categories(True) twice does not overwrite a
        # user's manual splitter adjustment.
        self._categories_visible = False
        outer = QVBoxLayout(self)
        if content_top_margin < 0:
            raise ValueError("content_top_margin must not be negative")
        outer.setContentsMargins(0, content_top_margin, 0, 0)
        outer.setSpacing(0)

        self.splitter = QSplitter(Qt.Horizontal, self)
        self.splitter.setObjectName("libraryBrowserSplitter")
        self.splitter.setChildrenCollapsible(False)
        outer.addWidget(self.splitter, 1)

        # Do not initially parent these controls to QSplitter: Qt treats every
        # direct child widget as a splitter pane.  Their panel layouts below
        # establish the intended three-pane hierarchy explicitly.
        self.library_filter = QLineEdit()
        self.category_filter = QLineEdit()
        self.cell_filter = QLineEdit()
        self.view_filter = QLineEdit()
        self.library_filter.setObjectName("libraryFilter")
        self.category_filter.setObjectName("categoryFilter")
        self.cell_filter.setObjectName("cellFilter")
        self.view_filter.setObjectName("viewFilter")

        self.library_list = LibraryTreeWidget()
        self.category_list = CategoryTreeWidget()
        self.cell_list = QListWidget()
        self.view_list = QListWidget()
        self.library_list.setObjectName("libraryList")
        self.category_list.setObjectName("categoryList")
        self.cell_list.setObjectName("cellList")
        self.view_list.setObjectName("viewList")

        for title, filter_edit, item_list in (
            ("Library", self.library_filter, self.library_list),
            ("Category", self.category_filter, self.category_list),
            ("Cell", self.cell_filter, self.cell_list),
            ("View", self.view_filter, self.view_list),
        ):
            filter_edit.setPlaceholderText("Filter")
            filter_edit.setClearButtonEnabled(True)
            item_list.setSelectionMode(QAbstractItemView.SingleSelection)
            panel = QWidget()
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(0, 0, 0, 0)
            panel_layout.setSpacing(4)
            panel_layout.addWidget(QLabel(title, panel))
            panel_layout.addWidget(filter_edit)
            panel_layout.addWidget(item_list, 1)
            if title != "Category":
                self.splitter.addWidget(panel)
            else:
                self._category_panel = panel
                panel.hide()

        for index in range(3):
            self.splitter.setStretchFactor(index, 1)

        self.show_categories = QCheckBox("Show Categories", self)
        self.show_categories.setObjectName("showCategories")
        self.show_categories.setChecked(False)
        outer.insertWidget(0, self.show_categories)

    @property
    def categories_visible(self):
        return self._categories_visible

    def set_categories_visible(self, enabled):
        enabled = bool(enabled)
        if enabled == self._categories_visible:
            return False
        self._categories_visible = enabled
        if enabled:
            self.splitter.insertWidget(1, self._category_panel)
            self._category_panel.show()
            self._equalize_categories_on_show = not self.isVisible()
            self._equalize_browser_columns()
        else:
            self._equalize_categories_on_show = False
            self._category_panel.hide()
            self._category_panel.setParent(self)
        return True

    def _equalize_browser_columns(self) -> None:
        """Give every currently displayed browser column the same width."""

        count = self.splitter.count()
        if count <= 0:
            return
        for index in range(count):
            self.splitter.setStretchFactor(index, 1)
        # QSplitter rescales these equal values to the available width after
        # accounting for its handles.  Using the current extent instead of
        # tiny values avoids individual panes being clamped by size hints.
        extent = max(1, self.splitter.width())
        self.splitter.setSizes([extent] * count)

    def showEvent(self, event) -> None:  # noqa: N802
        """Finish deferred equalization when Category was enabled off-screen."""

        super().showEvent(event)
        if self._equalize_categories_on_show and self.categories_visible:
            self._equalize_categories_on_show = False
            self._equalize_browser_columns()

    @contextmanager
    def blocked_lists(self) -> Iterator[None]:
        lists = (self.library_list, self.category_list, self.cell_list, self.view_list)
        prior = tuple(item_list.blockSignals(True) for item_list in lists)
        try:
            yield
        finally:
            for item_list, was_blocked in zip(lists, prior):
                item_list.blockSignals(was_blocked)

    def clear_rows(self):
        for item_list in (self.library_list, self.category_list, self.cell_list, self.view_list):
            item_list.clear()
