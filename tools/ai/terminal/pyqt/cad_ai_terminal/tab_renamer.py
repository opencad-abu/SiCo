"""Inline tab title editing and Qt-native rename actions."""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject, QTimer, Qt
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtWidgets import QAction, QLineEdit, QTabBar
from .appearance import MAXIMUM_SESSION_TITLE_LENGTH, _normalize_session_title


class TabRenamer(QObject):
    def __init__(self, tabs, current_session, queue_focus):
        super().__init__(tabs)
        self._tabs = tabs
        self._current_session = current_session
        self._queue_current_session_focus = queue_focus
        self._rename_editor = None
        self._rename_session = None
        bar = self._tab_bar = tabs.tabBar()
        bar.setContextMenuPolicy(Qt.ActionsContextMenu)
        bar.installEventFilter(self)
        action = QAction(QIcon.fromTheme("edit-rename"), "Rename Session", bar)
        action.setObjectName("renameSessionAction")
        action.setShortcut(QKeySequence("F2"))
        action.setShortcutContext(Qt.WindowShortcut)
        action.triggered.connect(self._queue_rename_current_session)
        bar.addAction(action)
        tabs.tabBarDoubleClicked.connect(self._rename_tab_requested)
        bar.tabMoved.connect(self._position_rename_editor)

    def tab_changed(self):
        if self._rename_session is not None:
            self._finish_session_rename()

    def removing(self, session):
        if session is self._rename_session:
            self.cancel(restore_focus=False)

    def eventFilter(self, watched, event) -> bool:
        if hasattr(self, "_tabs") and watched is self._tab_bar:
            if (
                event.type() == QEvent.MouseButtonPress
                and event.button() == Qt.RightButton
            ):
                index = watched.tabAt(event.pos())
                if index >= 0:
                    self._tabs.setCurrentIndex(index)
                else:
                    return True
            elif event.type() in (QEvent.Resize, QEvent.LayoutRequest):
                QTimer.singleShot(0, self._position_rename_editor)
        elif watched is self._rename_editor:
            if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
                self.cancel()
                return True
        return super().eventFilter(watched, event)

    def _rename_tab_requested(self, index: int) -> None:
        session = self._tabs.widget(index)
        if session is not None:
            self._begin_session_rename(session)

    def _queue_rename_current_session(self, *_arguments) -> None:
        session = self._current_session()
        if session is not None:
            # ActionsContextMenu owns a nested event loop. Start editing
            # only after that Qt-native popup has completely unwound.
            QTimer.singleShot(0, lambda: self._begin_session_rename(session))

    def _begin_session_rename(self, session) -> None:
        index = self._tabs.indexOf(session)
        if index < 0:
            return
        self.cancel(restore_focus=False)
        tab_bar = self._tabs.tabBar()
        editor = QLineEdit(tab_bar)
        editor.setObjectName("sessionNameEditor")
        editor.setAccessibleName("Session name")
        editor.setMaxLength(MAXIMUM_SESSION_TITLE_LENGTH)
        editor.setText(session.title)
        editor.selectAll()
        editor.editingFinished.connect(self._finish_session_rename)
        editor.installEventFilter(self)
        self._rename_editor = editor
        self._rename_session = session
        self._position_rename_editor()
        editor.show()
        editor.raise_()
        editor.setFocus(Qt.MouseFocusReason)

    def _position_rename_editor(self, *_arguments) -> None:
        editor = self._rename_editor
        session = self._rename_session
        if editor is None or session is None:
            return
        index = self._tabs.indexOf(session)
        if index < 0:
            self.cancel(restore_focus=False)
            return
        tab_bar = self._tabs.tabBar()
        rectangle = tab_bar.tabRect(index)
        left_margin = 4
        right_margin = 4
        left_button = tab_bar.tabButton(index, QTabBar.LeftSide)
        right_button = tab_bar.tabButton(index, QTabBar.RightSide)
        if left_button is not None and left_button.isVisible():
            left_margin += left_button.width() + 2
        if right_button is not None and right_button.isVisible():
            right_margin += right_button.width() + 2
        editor.setGeometry(rectangle.adjusted(left_margin, 2, -right_margin, -2))

    def _finish_session_rename(self, *_arguments) -> None:
        editor = self._rename_editor
        session = self._rename_session
        if editor is None:
            return
        title = _normalize_session_title(editor.text())
        self._clear_session_rename()
        if title and self._tabs.indexOf(session) >= 0:
            session.title = title
            index = self._tabs.indexOf(session)
            if index >= 0:
                self._tabs.setTabText(index, title)
                self._tabs.setTabToolTip(index, title)
        self._queue_current_session_focus()

    def cancel(self, *, restore_focus: bool = True) -> None:
        if self._rename_editor is None:
            return
        self._clear_session_rename()
        if restore_focus:
            self._queue_current_session_focus()

    def _clear_session_rename(self) -> None:
        editor = self._rename_editor
        self._rename_editor = None
        self._rename_session = None
        if editor is not None:
            editor.blockSignals(True)
            editor.removeEventFilter(self)
            editor.hide()
            editor.deleteLater()
