"""Label grouping and drop intents inside the existing session-history pane."""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QShortcut,
    QTreeWidget,
    QTreeWidgetItem,
)

from .navigation_rows import KIND_ROLE, session_item
from .navigation_status import STATUS_DOT
from .row_stripes import enable_row_stripes

LABEL_ROLE = Qt.UserRole + 5

KIND_LABEL = "label"
KIND_UNCLASSIFIED = "unclassified"
KIND_SESSION = "session"
UNCLASSIFIED_KEY = "__unclassified__"


def _kind(item):
    return item.data(0, KIND_ROLE) if item is not None else None


class LabeledHistory(QTreeWidget):
    """The lower navigation pane, grouped by labels only when labels exist."""

    def __init__(self, navigation):
        super().__init__()
        self.navigation = navigation
        self._labels = []
        self._known_labels = set()
        self._expanded = None
        self._drag_source = None
        self.setObjectName("sessionHistory")
        self.setHeaderHidden(True)
        self.setIconSize(QSize(STATUS_DOT, STATUS_DOT))
        self.setRootIsDecorated(True)
        self.setIndentation(16)
        self.setVerticalScrollMode(self.ScrollPerPixel)
        self.setExpandsOnDoubleClick(False)
        self.setSelectionMode(self.SingleSelection)
        self.setDropIndicatorShown(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        enable_row_stripes(self)
        self.itemDoubleClicked.connect(self._activate, Qt.QueuedConnection)
        self.currentItemChanged.connect(self.navigation.select)
        for key in (Qt.Key_Return, Qt.Key_Enter):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda: self._activate(self.currentItem()))
        self.customContextMenuRequested.connect(self._menu)
        self._set_drag(False)

    def _set_drag(self, enabled):
        self.setDragEnabled(enabled)
        self.setAcceptDrops(enabled)
        self.setDragDropMode(
            QAbstractItemView.DragDrop if enabled else QAbstractItemView.NoDragDrop
        )
        self.setDefaultDropAction(Qt.MoveAction)

    def _menu(self, position):
        item = self.itemAt(position)
        if item is None:
            self.navigation._label_blank_menu(self, position)
            return
        self.navigation._menu(position, self)

    def _activate(self, item, _column=0):
        if item is None:
            return
        if _kind(item) in {KIND_LABEL, KIND_UNCLASSIFIED}:
            item.setExpanded(not item.isExpanded())
            return
        self.navigation.browse(item)

    def startDrag(self, actions):
        self._drag_source = self.currentItem()
        timer = getattr(self.navigation, "timer", None)
        active = timer is not None and timer.isActive()
        self.navigation._label_dragging = True
        if active:
            timer.stop()
        try:
            super().startDrag(actions)
        finally:
            self.navigation._label_dragging = False
            if active:
                timer.start()
            refresh = getattr(self.navigation, "refresh", None)
            if refresh is not None:
                refresh()

    def dropEvent(self, event):
        target = self.itemAt(event.pos())
        intent = self.drop_intent(self._drag_source or self.currentItem(), target,
                                  self.dropIndicatorPosition())
        self._drag_source = None
        if intent is None:
            event.ignore()
            return
        event.setDropAction(Qt.MoveAction)
        event.accept()
        self.navigation._label_drop(intent)

    def drop_intent(self, source, target, position):
        if source is None or source.treeWidget() is not self:
            return None
        source_kind = _kind(source)
        if source_kind == KIND_LABEL:
            if target is None or _kind(target) != KIND_LABEL:
                return None
            source_id = source.data(0, LABEL_ROLE)
            target_id = target.data(0, LABEL_ROLE)
            if source_id == target_id:
                return None
            ids = [row["id"] for row in self._labels if row["id"] != source_id]
            if source_id not in self._labels_by_id() or target_id not in ids:
                return None
            index = ids.index(target_id)
            if position != QAbstractItemView.AboveItem:
                index += 1
            ids.insert(index, source_id)
            return None if ids == [row["id"] for row in self._labels] else {
                "action": "reorder_labels", "label_ids": ids,
            }
        if source_kind != KIND_SESSION:
            return None
        session_id = source.data(0, Qt.UserRole)
        if not session_id:
            return None
        if target is None:
            return {"action": "move_sessions", "session_ids": [session_id],
                    "label_id": None, "before_id": None}
        target_kind = _kind(target)
        if target_kind == KIND_LABEL:
            return {"action": "move_sessions", "session_ids": [session_id],
                    "label_id": target.data(0, LABEL_ROLE), "before_id": None}
        if target_kind == KIND_UNCLASSIFIED:
            return {"action": "move_sessions", "session_ids": [session_id],
                    "label_id": None, "before_id": None}
        if target_kind != KIND_SESSION or target is source:
            return None
        if source.parent() is target.parent() and position == QAbstractItemView.OnItem:
            return None
        parent = target.parent()
        label_id = parent.data(0, LABEL_ROLE) if _kind(parent) == KIND_LABEL else None
        if position == QAbstractItemView.AboveItem:
            before_id = target.data(0, Qt.UserRole)
        else:
            index = parent.indexOfChild(target) + 1 if parent is not None else 0
            sibling = parent.child(index) if parent is not None and index < parent.childCount() else None
            before_id = sibling.data(0, Qt.UserRole) if sibling is not None else None
        return {"action": "move_sessions", "session_ids": [session_id],
                "label_id": label_id, "before_id": before_id}

    def _labels_by_id(self):
        return {row["id"] for row in self._labels}

    def update_snapshot(self, rows, layout, *, browsing, selected, ended):
        rows = [dict(row) for row in rows]
        labels = list((layout or {}).get("labels", ()))
        self._labels = labels
        self._set_drag(bool(labels))
        by_id = {row["id"]: row for row in rows}
        self._label_map = {
            session_id: label["id"]
            for label in labels for session_id in label.get("sessions", ())
        }
        old_expanded = self._expansion()
        scroll = self.verticalScrollBar().value()
        self.blockSignals(True)
        try:
            self.clear()
            if labels:
                for label in labels:
                    visible = [
                        session_id for session_id in label.get("sessions", ())
                        if session_id in by_id
                    ]
                    group = self._group(label["name"], len(visible), KIND_LABEL,
                                        label_id=label["id"])
                    self.addTopLevelItem(group)
                    for session_id in visible:
                        self._add_session(group, by_id[session_id], browsing, ended,
                                          label["id"])
                unclassified = [
                    row for row in rows if row["id"] not in self._label_map
                ]
                preferred = [
                    session_id for session_id in (layout or {}).get("loose_order", ())
                    if session_id in {row["id"] for row in unclassified}
                ]
                unclassified.sort(key=lambda row: (
                    preferred.index(row["id"]) if row["id"] in preferred else len(preferred),
                    -row.get("modified", 0),
                ))
                group = self._group("未归类", len(unclassified), KIND_UNCLASSIFIED)
                self.addTopLevelItem(group)
                for row in unclassified:
                    self._add_session(group, row, browsing, ended, None)
            else:
                for row in rows:
                    self._add_session(None, row, browsing, ended, None)
            self._restore_expansion(old_expanded, labels)
            if selected:
                self._select_id(selected)
        finally:
            self.blockSignals(False)
        self.verticalScrollBar().setValue(scroll)

    def _add_session(self, group, row, browsing, ended, label_id):
        item = session_item(
            row, live="", browsing=browsing, opened_here=False,
            ended=ended, activity="idle",
            line_spacing=self.fontMetrics().lineSpacing(),
            item_factory=QTreeWidgetItem,
        )
        item.setData(0, KIND_ROLE, KIND_SESSION)
        item.setData(0, LABEL_ROLE, label_id or "")
        item.setFlags(item.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
        if not item.flags() & Qt.ItemIsEnabled:
            item.setFlags(item.flags() & ~Qt.ItemIsDragEnabled & ~Qt.ItemIsDropEnabled)
        if group is None:
            self.addTopLevelItem(item)
        else:
            group.addChild(item)

    @staticmethod
    def _group(name, count, kind, *, label_id=""):
        item = QTreeWidgetItem(["%s  (%d)" % (name, count)])
        item.setData(0, KIND_ROLE, kind)
        item.setData(0, LABEL_ROLE, label_id)
        item.setFlags(item.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
        item.setToolTip(0, "双击展开/折叠；右键可管理标签")
        return item

    def _expansion(self):
        if self._expanded is None:
            return None
        result = set()
        for index in range(self.topLevelItemCount()):
            item = self.topLevelItem(index)
            if _kind(item) in {KIND_LABEL, KIND_UNCLASSIFIED} and item.isExpanded():
                result.add(item.data(0, LABEL_ROLE) or UNCLASSIFIED_KEY)
        return result

    def _restore_expansion(self, old_expanded, labels):
        current = {row["id"] for row in labels}
        if old_expanded is None:
            self._expanded = set(current) | {UNCLASSIFIED_KEY}
        else:
            self._expanded = set(old_expanded)
            self._expanded.update(current - self._known_labels)
        self._known_labels = current
        for index in range(self.topLevelItemCount()):
            item = self.topLevelItem(index)
            if _kind(item) not in {KIND_LABEL, KIND_UNCLASSIFIED}:
                continue
            key = item.data(0, LABEL_ROLE) or UNCLASSIFIED_KEY
            item.setExpanded(key in self._expanded)

    def _select_id(self, session_id):
        for item in self.session_items():
            if item.data(0, Qt.UserRole) == session_id:
                self.setCurrentItem(item)
                return True
        return False

    def session_items(self):
        for index in range(self.topLevelItemCount()):
            top = self.topLevelItem(index)
            if _kind(top) == KIND_SESSION:
                yield top
            for child_index in range(top.childCount()):
                child = top.child(child_index)
                if _kind(child) == KIND_SESSION:
                    yield child
