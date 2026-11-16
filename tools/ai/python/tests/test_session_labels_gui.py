"""Offscreen contracts for labels integrated into the session-history pane."""

# Fixture imports intentionally shadow pytest parameter names at collection time.
# ruff: noqa: F401, F811, I001

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QAbstractItemView, QApplication, QTreeWidget
from agent_navigation_contract_support import app, window
from sico_ui.navigation_labels import (
    KIND_LABEL,
    KIND_ROLE,
    KIND_SESSION,
    KIND_UNCLASSIFIED,
    LABEL_ROLE,
    LabeledHistory,
)
from sico_ui.navigation_rows import CHILD_KIND


class NavigationStub:
    def select(self, item, previous=None):
        self.selected = item

    def browse(self, item, _column=None):
        self.browsed = item

    def _menu(self, _position, _tree):
        pass

    def _label_blank_menu(self, _tree, _position):
        pass

    def _label_drop(self, intent):
        self.dropped = intent


def layout():
    return {
        "revision": "r1",
        "labels": [
            {"id": "l_" + "a" * 32, "name": "PDK",
             "sessions": ["a", "b"]},
            {"id": "l_" + "b" * 32, "name": "Simulation",
             "sessions": ["c"]},
        ],
        "loose_order": ["d"],
        "error": "",
    }


def rows():
    return [
        {"id": "a", "modified": 4, "name": "A"},
        {"id": "b", "modified": 3, "name": "B"},
        {"id": "c", "modified": 2, "name": "C"},
        {"id": "d", "modified": 1, "name": "D"},
    ]


def test_history_without_labels_keeps_the_original_flat_rows(app):
    view = LabeledHistory(NavigationStub())
    view.update_snapshot(rows(), {"labels": [], "loose_order": [], "error": ""},
                         browsing=None, selected=None, ended=set())
    top = [view.topLevelItem(index) for index in range(view.topLevelItemCount())]
    assert [item.data(0, Qt.UserRole) for item in top] == ["a", "b", "c", "d"]
    assert all(item.data(0, KIND_ROLE) == KIND_SESSION for item in top)
    assert not view.dragEnabled() and not view.acceptDrops()
    view.deleteLater()


def test_history_groups_only_when_labels_exist(app):
    view = LabeledHistory(NavigationStub())
    view.update_snapshot(rows(), layout(), browsing=None, selected=None, ended=set())
    top = [view.topLevelItem(index) for index in range(view.topLevelItemCount())]
    assert [item.text(0) for item in top] == [
        "PDK  (2)", "Simulation  (1)", "未归类  (1)",
    ]
    assert [item.data(0, KIND_ROLE) for item in top] == [
        KIND_LABEL, KIND_LABEL, KIND_UNCLASSIFIED,
    ]
    assert [top[0].child(index).data(0, Qt.UserRole) for index in range(2)] == ["a", "b"]
    assert top[1].child(0).data(0, Qt.UserRole) == "c"
    assert top[2].child(0).data(0, Qt.UserRole) == "d"
    assert [item.data(0, Qt.UserRole) for item in view.session_items()] == ["a", "b", "c", "d"]
    assert view.dragEnabled() and view.acceptDrops()
    view.deleteLater()


def test_history_drag_intents_use_the_service_commands(app):
    nav = NavigationStub()
    view = LabeledHistory(nav)
    view.update_snapshot(rows(), layout(), browsing=None, selected=None, ended=set())
    pdk, simulation, unclassified = (
        view.topLevelItem(0), view.topLevelItem(1), view.topLevelItem(2),
    )
    a, b = pdk.child(0), pdk.child(1)
    c = simulation.child(0)
    assert view.drop_intent(pdk, simulation, QAbstractItemView.BelowItem) == {
        "action": "reorder_labels",
        "label_ids": ["l_" + "b" * 32, "l_" + "a" * 32],
    }
    assert view.drop_intent(b, a, QAbstractItemView.AboveItem) == {
        "action": "move_sessions", "session_ids": ["b"],
        "label_id": pdk.data(0, LABEL_ROLE), "before_id": "a",
    }
    assert view.drop_intent(a, simulation, QAbstractItemView.OnItem) == {
        "action": "move_sessions", "session_ids": ["a"],
        "label_id": simulation.data(0, LABEL_ROLE), "before_id": None,
    }
    assert view.drop_intent(c, unclassified, QAbstractItemView.OnItem) == {
        "action": "move_sessions", "session_ids": ["c"],
        "label_id": None, "before_id": None,
    }
    assert [pdk.child(index).data(0, Qt.UserRole) for index in range(2)] == ["a", "b"]
    view.deleteLater()


def test_child_agent_rows_are_not_draggable_or_label_targets(app):
    view = LabeledHistory(NavigationStub())
    data = [{
        "id": "a", "modified": 4, "name": "A",
        "children": [{
            "thread_id": "child-a", "name": "window_reader",
            "status": "started", "children": [],
        }],
    }]
    view.update_snapshot(
        data,
        {"labels": [{"id": "l_" + "a" * 32, "name": "PDK",
                     "sessions": ["a"]}],
         "loose_order": [], "error": ""},
        browsing=None, selected=None, ended=set(),
    )
    parent = view.topLevelItem(0).child(0)
    child = parent.child(0)
    assert child.data(0, KIND_ROLE) == CHILD_KIND
    assert not child.flags() & Qt.ItemIsDragEnabled
    assert not child.flags() & Qt.ItemIsDropEnabled
    assert view.drop_intent(child, view.topLevelItem(0), QAbstractItemView.OnItem) is None
    view.deleteLater()


def test_drag_pauses_navigation_refresh_until_the_drag_finishes(app, monkeypatch):
    class Timer:
        def __init__(self):
            self.active = True
            self.stopped = 0
            self.started = 0

        def isActive(self):
            return self.active

        def stop(self):
            self.active = False
            self.stopped += 1

        def start(self):
            self.active = True
            self.started += 1

    nav = NavigationStub()
    nav.timer = Timer()
    nav._label_dragging = False
    refreshes = []
    nav.refresh = lambda: refreshes.append(True)
    view = LabeledHistory(nav)
    monkeypatch.setattr(QTreeWidget, "startDrag", lambda _self, _actions: None)
    view.startDrag(Qt.MoveAction)
    assert nav.timer.stopped == 1 and nav.timer.started == 1
    assert refreshes == [True]
    assert nav._label_dragging is False
    view.deleteLater()


def test_navigation_has_no_separate_label_tab(window):
    assert window.navigation.tabText(0) == "会话历史"
    assert window.navigation.tabText(1).startswith("任务队列")
    assert all(window.navigation.tabText(index) != "标签"
               for index in range(window.navigation.count()))
    assert isinstance(window.navigation.list, LabeledHistory)
    QApplication.processEvents()
