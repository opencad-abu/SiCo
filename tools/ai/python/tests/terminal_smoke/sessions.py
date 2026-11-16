"""Real terminal independent PTYs, completion and popup teardown assertions."""

from PyQt5 import QtWidgets
from PyQt5.QtCore import QPoint, QTimer
from PyQt5.QtGui import QContextMenuEvent
from terminal_smoke.shutdown import verify_running_shutdown


def verify_sessions(self, window, terminal, surface, tabs):
    new_session = surface.findChild(QtWidgets.QAction, "newSessionAction")
    assert new_session is not None
    first_pid = terminal.getShellPID()
    position = QPoint(30, 30)
    new_session_event = QContextMenuEvent(
        QContextMenuEvent.Mouse, position, surface.mapToGlobal(position)
    )

    def trigger_new_session_from_popup():
        popup = self.activePopupWidget()
        assert isinstance(popup, QtWidgets.QMenu)
        popup.actions()[0].trigger()

    QTimer.singleShot(0, trigger_new_session_from_popup)
    assert self.sendEvent(surface, new_session_event)
    for _ in range(40):
        self.processEvents()
        if tabs.count() == 2:
            break
    assert tabs.count() == 2
    terminals = window.findChildren(QtWidgets.QWidget, "aiTerminal")
    assert len(terminals) == 2
    second = next(item for item in terminals if item is not terminal)
    assert sorted(tabs.tabText(index) for index in range(tabs.count())) == [
        "Layout debug",
        "会话 2",
    ]
    assert first_pid > 0 and second.getShellPID() > 0
    assert first_pid != second.getShellPID()

    # A naturally completed session removes only its own tab.
    tabs.setCurrentIndex(tabs.indexOf(terminal.parentWidget()))
    window._finish_session()
    for _ in range(20):
        self.processEvents()
        if tabs.count() == 1:
            break
    assert tabs.count() == 1
    assert window._terminal is second

    # Recreate a session for the popup teardown regression below.
    window._add_session_from_menu()
    for _ in range(40):
        self.processEvents()
        if tabs.count() == 2:
            break
    assert tabs.count() == 2
    third = window._terminal
    assert third is not None and third is not second

    # Toolbar operations are routed to the selected tab only.
    first_font = third.getTerminalFont().pointSize()
    second_font = second.getTerminalFont().pointSize()
    tabs.setCurrentIndex(tabs.indexOf(second.parentWidget()))
    zoom_in = window.findChild(QtWidgets.QAction, "zoomInAction")
    assert zoom_in is not None
    zoom_in.trigger()
    assert third.getTerminalFont().pointSize() == first_font
    assert second.getTerminalFont().pointSize() > second_font

    # ActionsContextMenu runs a synchronous nested event loop. Closing one
    # tab must close that popup and defer QTermWidget deletion until the
    # nested loop has unwound, while the other session remains alive.
    third_surface = third.focusProxy() or third
    third_event = QContextMenuEvent(
        QContextMenuEvent.Mouse,
        position,
        third_surface.mapToGlobal(position),
    )
    third_session = third.parentWidget()
    QTimer.singleShot(
        0, lambda: window._closing.request_close_session(third_session, confirm=False)
    )
    assert self.sendEvent(third_surface, third_event)
    for _ in range(20):
        self.processEvents()
    assert tabs.count() == 1
    assert window._terminal is second
    assert second.getShellPID() > 0

    # The final completed tab remains visible so an immediate launcher
    # failure cannot make the entire window flash and disappear.
    window._finish_session()
    for _ in range(20):
        self.processEvents()
    assert tabs.count() == 1
    assert tabs.widget(0).finished
    assert window.isVisible()
    status = window.findChild(QtWidgets.QLabel, "sessionStatus")
    assert status is not None and status.text().strip() == "已退出"
    verify_running_shutdown(self, window)
    return 0
