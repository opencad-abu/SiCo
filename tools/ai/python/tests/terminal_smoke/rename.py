"""Real Qt-native tab rename and cancellation assertions."""

from PyQt5 import QtWidgets
from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtGui import QContextMenuEvent, QKeyEvent
from PyQt5.QtTest import QTest


def verify_rename(self, window, tabs):
    tab_bar = tabs.tabBar()
    # Rename from the Qt-native tab context menu. The inline editor avoids
    # another modal event loop after the context menu closes.
    tab_position = tab_bar.tabRect(0).center()
    rename_event = QContextMenuEvent(
        QContextMenuEvent.Mouse,
        tab_position,
        tab_bar.mapToGlobal(tab_position),
    )

    def trigger_rename_from_popup():
        popup = self.activePopupWidget()
        assert isinstance(popup, QtWidgets.QMenu)
        assert [action.text() for action in popup.actions()] == ["重命名会话"]
        popup.setActiveAction(popup.actions()[0])
        QTest.keyClick(popup, Qt.Key_Return)

    QTimer.singleShot(0, trigger_rename_from_popup)
    assert self.sendEvent(tab_bar, rename_event)
    for _ in range(20):
        self.processEvents()
        editor = tab_bar.findChild(QtWidgets.QLineEdit, "sessionNameEditor")
        if editor is not None and editor.isVisible():
            break
    assert editor is not None and editor.isVisible()
    editor.setText("  Layout    debug  ")
    editor.editingFinished.emit()
    self.processEvents()
    assert tabs.tabText(0) == "Layout debug"
    assert tabs.tabToolTip(0) == "Layout debug"
    assert tabs.widget(0).title == "Layout debug"
    for _ in range(5):
        self.processEvents()

    # Double-click also starts editing; Escape leaves the existing title.
    tabs.tabBarDoubleClicked.emit(0)
    editor = window._renamer._rename_editor
    assert editor is not None
    editor.setText("Discarded title")
    escape = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    assert window._renamer.eventFilter(editor, escape)
    self.processEvents()
    assert tabs.tabText(0) == "Layout debug"
