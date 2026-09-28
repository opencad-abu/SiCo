"""Own selected source-cell navigation and adapt browser activation to drafts."""

from __future__ import annotations
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtWidgets import QListWidgetItem, QMenu


class CellSelection:
    def __init__(self, *, browser, queue, title, legacy, drafts, form,
                 invalidate, enqueue, advance, remove_defaults):
        self._browser = browser
        self._queue = queue
        self._title = title
        self._legacy = legacy
        self.drafts = drafts
        self._form = form
        self._invalidate = invalidate
        self._enqueue = enqueue
        self._advance = advance
        self._remove_defaults = remove_defaults
        self.active = None
        self.queue_mode = False

    def view_menu(self, position: QPoint) -> None:
        item = self._browser.view_list.itemAt(position)
        if item is None:
            return
        self._browser.view_list.setCurrentItem(item)
        menu = QMenu(self._browser.view_list)
        select = menu.addAction("Select")
        if menu.exec_(self._browser.view_list.viewport().mapToGlobal(position)) == select:
            self.select()

    def cell_menu(self, position: QPoint) -> None:
        item = self._queue.itemAt(position)
        if item is None:
            return
        self._queue.setCurrentItem(item)
        menu = QMenu(self._queue)
        delete = menu.addAction("Delete")
        if menu.exec_(self._queue.viewport().mapToGlobal(position)) == delete:
            self.delete(item)

    def select(self) -> None:
        item = self._browser.view_list.currentItem()
        payload = None if item is None else item.data(Qt.UserRole)
        # Scripted callers historically inserted a qualified tuple directly
        # into the View list. Prefer that explicit payload when present;
        # catalog-backed items are represented by CatalogView and use the
        # reusable browser's qualified selection.
        if isinstance(payload, tuple) and len(payload) == 3:
            key = tuple(str(value) for value in payload)
        else:
            key = self._browser.selection
        if key is None:
            return
        for row in range(self._queue.count()):
            existing = self._queue.item(row)
            if existing.data(Qt.UserRole) == key:
                self._queue.setCurrentItem(existing)
                return
        self.save()
        template = (self.drafts.view(self.active, self._form.dialect).simulator
                    if self.active is not None else None)
        self._invalidate("a source cell was added")
        self.queue_mode = True
        self.drafts.add(key, self._form.dialect, simulator=template)
        selected = QListWidgetItem("/".join(key))
        selected.setData(Qt.UserRole, key)
        selected.setToolTip("/".join(key))
        self._queue.addItem(selected)
        self._queue.setCurrentItem(selected)
        # First selection initializes both supported simulator dialects.  The
        # explicit argument keeps the lower-level queue helper's historical
        # single-dialect behavior for scripted integrations.
        self._enqueue(key, dialects=("spectre", "hspiceD"))

    def activated(
        self, library: str, cell: str, view: str
    ) -> None:
        """Adapt the reusable browser activation to the MTS source queue."""

        if self._browser.selection != (library, cell, view):
            return
        self.select()

    def delete(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.UserRole)
        row = self._queue.row(item)
        self.save()
        self._invalidate("a source cell was deleted")
        self._queue.blockSignals(True)
        try:
            self._queue.takeItem(row)
            if isinstance(key, tuple):
                self._remove_defaults(key)
                self.drafts.remove(key)
            self.active = None
            if self._queue.count():
                self._queue.setCurrentRow(min(row, self._queue.count() - 1))
        finally:
            self._queue.blockSignals(False)
        current = self._queue.currentItem()
        if current is not None:
            self.current_changed(current, None)
        else:
            self._form.clear()
        self._advance()

    def current_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if self._form.loading:
            return
        self.save()
        key = None if current is None else current.data(Qt.UserRole)
        if not isinstance(key, tuple) or len(key) != 3:
            self.active = None
            self.update_title()
            return
        self.active = key
        self._form.load(self.drafts.view(key, self._form.dialect))

    def update_title(self) -> None:
        key = self.active
        if isinstance(key, tuple) and len(key) == 3:
            identity = "/".join(str(value) for value in key)
            self._title.setTitle(identity)
            self._title.setToolTip(identity)
        else:
            self._title.setTitle("No Source Cell Selected")
            self._title.setToolTip("")

    def save(self, *, dialect=None, edited=False) -> None:
        if self._form.loading or self.active is None:
            return
        self.drafts.save(self._form.capture(self.active, dialect=dialect), edited=edited)

    def browser_changed(self, selection) -> None:
        """Mirror the common browser selection into legacy hidden fields."""

        if not isinstance(selection, tuple) or len(selection) != 3:
            self._legacy[0].clear()
            self._legacy[1].clear()
            self._legacy[2].clear()
            return
        self._legacy[0].setText(str(selection[0]))
        self._legacy[1].setText(str(selection[1]))
        self._legacy[2].setText(str(selection[2]))
