"""Inline tab rename editing, cancellation and Qt-native context actions."""

from __future__ import annotations

import pytest
from PyQt5.QtCore import QEvent, QPoint, Qt
from PyQt5.QtGui import QKeyEvent, QMouseEvent
from PyQt5.QtWidgets import QApplication, QTabWidget, QWidget

import terminal_fixtures  # noqa: F401  # installs the PyQt package path
from cad_ai_terminal import tab_renamer as module
from cad_ai_terminal.tab_renamer import TabRenamer


class Session(QWidget):
    def __init__(self, title):
        super().__init__()
        self.title = title


@pytest.fixture
def renamed_tabs():
    app = QApplication.instance() or QApplication([])
    tabs = QTabWidget()
    tabs.setTabsClosable(True)
    sessions = [Session("one"), Session("two")]
    for session in sessions:
        tabs.addTab(session, session.title)
    tabs.setCurrentIndex(0)
    tabs.show()
    app.processEvents()
    focus = []
    renamer = TabRenamer(tabs, lambda: tabs.currentWidget(), lambda: focus.append(True))
    yield tabs, sessions, renamer, focus
    tabs.close()
    tabs.deleteLater()
    app.processEvents()


def test_begin_edit_normalize_finish_and_focus(renamed_tabs):
    _tabs, sessions, renamer, focus = renamed_tabs
    renamer._begin_session_rename(sessions[0])
    editor = renamer._rename_editor
    assert editor is not None
    assert editor.objectName() == "sessionNameEditor"
    assert editor.accessibleName() == "会话名称"
    assert editor.text() == "one"
    assert editor.maxLength() == module.MAXIMUM_SESSION_TITLE_LENGTH
    assert editor.isVisible()

    editor.setText("  renamed   session  ")
    renamer._finish_session_rename()
    assert sessions[0].title == "renamed session"
    assert _tabs.tabText(0) == "renamed session"
    assert _tabs.tabToolTip(0) == "renamed session"
    assert focus == [True]
    assert renamer._rename_editor is None and renamer._rename_session is None
    renamer._finish_session_rename()
    assert focus == [True]


def test_empty_title_does_not_change_session_and_cancel_restores_focus(renamed_tabs):
    _tabs, sessions, renamer, focus = renamed_tabs
    renamer._begin_session_rename(sessions[0])
    renamer._rename_editor.setText(" \t ")
    renamer._finish_session_rename()
    assert sessions[0].title == "one"
    assert focus == [True]

    renamer._begin_session_rename(sessions[0])
    renamer.cancel()
    assert renamer._rename_editor is None
    assert focus == [True, True]
    renamer.cancel()
    assert focus == [True, True]


def test_removing_and_tab_change_finish_or_cancel_the_editor(renamed_tabs):
    _tabs, sessions, renamer, focus = renamed_tabs
    renamer._begin_session_rename(sessions[0])
    renamer.removing(sessions[1])
    assert renamer._rename_editor is not None
    renamer.removing(sessions[0])
    assert renamer._rename_editor is None
    assert focus == []

    renamer._begin_session_rename(sessions[1])
    renamer._rename_editor.setText("changed")
    renamer.tab_changed()
    assert sessions[1].title == "changed"
    assert focus == [True]


def test_escape_cancels_and_tab_bar_right_click_selects_or_blocks(renamed_tabs):
    tabs, _sessions, renamer, _focus = renamed_tabs
    renamer._begin_session_rename(tabs.widget(0))
    editor = renamer._rename_editor
    escape = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    assert renamer.eventFilter(editor, escape) is True
    assert renamer._rename_editor is None

    bar = tabs.tabBar()
    tabs.setCurrentIndex(1)
    select = QMouseEvent(
        QEvent.MouseButtonPress, bar.tabRect(0).center(),
        Qt.RightButton, Qt.RightButton, Qt.NoModifier,
    )
    assert renamer.eventFilter(bar, select) is False
    assert tabs.currentIndex() == 0
    blank = QMouseEvent(
        QEvent.MouseButtonPress, QPoint(-1, -1),
        Qt.RightButton, Qt.RightButton, Qt.NoModifier,
    )
    assert renamer.eventFilter(bar, blank) is True


def test_double_click_and_queued_current_rename_routes(renamed_tabs, monkeypatch):
    tabs, _sessions, renamer, focus = renamed_tabs
    renamer._rename_tab_requested(99)
    assert renamer._rename_editor is None
    renamer._rename_tab_requested(1)
    assert renamer._rename_editor is not None
    renamer.cancel(restore_focus=False)

    monkeypatch.setattr(module.QTimer, "singleShot",
                        staticmethod(lambda _delay, callback: callback()))
    renamer._queue_rename_current_session()
    assert renamer._rename_editor.text() == "one"
    renamer.cancel()
    assert focus == [True]


def test_positioning_cancels_when_session_is_no_longer_in_the_tabs(renamed_tabs):
    tabs, sessions, renamer, focus = renamed_tabs
    renamer._begin_session_rename(sessions[0])
    tabs.removeTab(0)
    renamer._position_rename_editor()
    assert renamer._rename_editor is None
    assert focus == []
