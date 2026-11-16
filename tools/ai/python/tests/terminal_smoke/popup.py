"""Real QTermWidget popup, selection and clipboard assertions."""

import gc

from PyQt5 import QtWidgets
from PyQt5.QtCore import QEvent, QPoint, Qt, QTimer
from PyQt5.QtGui import QContextMenuEvent, QMouseEvent


def verify_popup(self, window):
    terminal = window.findChild(QtWidgets.QWidget, "aiTerminal")
    assert terminal is not None
    for _ in range(10000):
        self.processEvents()
        if terminal.historyLinesCount() > 0:
            break
    gc.collect()
    surface = terminal.focusProxy() or terminal
    start = QPoint(8, 8)
    end = QPoint(180, 8)
    assert self.sendEvent(
        surface,
        QMouseEvent(
            QMouseEvent.MouseButtonPress,
            start,
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ),
    )
    assert self.sendEvent(
        surface,
        QMouseEvent(
            QMouseEvent.MouseMove,
            end,
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        ),
    )
    assert self.sendEvent(
        surface,
        QMouseEvent(
            QMouseEvent.MouseButtonRelease,
            end,
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        ),
    )
    self.processEvents()
    selected = terminal.selectedText(False)
    assert selected
    assert surface.contextMenuPolicy() == Qt.ActionsContextMenu
    action_texts = [action.text() for action in surface.actions()]
    assert action_texts == ["新建会话", "", "复制", "粘贴", "全选", "", "查找"]
    position = QPoint(30, 30)
    event = QContextMenuEvent(QContextMenuEvent.Mouse, position, surface.mapToGlobal(position))
    popup_seen = []

    def inspect_and_close_popup():
        popup = self.activePopupWidget()
        assert isinstance(popup, QtWidgets.QMenu)
        popup_seen.append([action.text() for action in popup.actions()])
        popup.close()

    QTimer.singleShot(0, inspect_and_close_popup)
    assert self.sendEvent(surface, event)
    self.processEvents()
    assert popup_seen == [["新建会话", "", "复制", "粘贴", "全选", "", "查找"]]
    copy_action = window.findChild(QtWidgets.QAction, "copyAction")
    assert copy_action is not None and copy_action.isEnabled()
    self.clipboard().clear()
    copy_action.trigger()
    assert self.clipboard().text() == selected

    tabs = window.findChild(QtWidgets.QTabWidget, "terminalTabs")
    assert tabs is not None and tabs.count() == 1
    assert tabs.tabText(0) == "会话 1"
    tab_bar = tabs.tabBar()
    assert tab_bar.contextMenuPolicy() == Qt.ActionsContextMenu
    rename_action = tab_bar.findChild(QtWidgets.QAction, "renameSessionAction")
    assert rename_action is not None and rename_action.shortcut().toString() == "F2"
    assert [action.text() for action in tab_bar.actions()] == ["重命名会话"]
    right_press = QMouseEvent(
        QEvent.MouseButtonPress,
        tab_bar.tabRect(0).center(),
        Qt.RightButton,
        Qt.RightButton,
        Qt.NoModifier,
    )
    window._renamer.eventFilter(tab_bar, right_press)
    assert tabs.currentIndex() == 0

    return terminal, surface, tabs
