"""TerminalWindow routes the selected tab without constructing QTermWidget."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt5.QtWidgets import QApplication, QLabel, QWidget

import terminal_fixtures  # noqa: F401  # installs the PyQt package path
from cad_ai_terminal.terminal_window import TerminalWindow


class FakeSession(QWidget):
    instances = []

    def __init__(self, number, new_session, finished, status, parent):
        super().__init__(parent)
        self.number = number
        self.title = f"会话 {number}"
        self.terminal = object()
        self.status = ("运行中", "#00aa00")
        self.running = False
        self.released = 0
        self.invoked = []
        self.paste = []
        self.new_enabled = []
        self.focused = 0
        self.finished = False
        self.new_session = new_session
        self.finished_callback = finished
        self.status_callback = status
        type(self).instances.append(self)

    def invoke(self, method):
        self.invoked.append(method)

    def paste_clipboard(self):
        self.invoked.append("pasteClipboard")

    def interrupt(self):
        self.invoked.append("interrupt")

    def set_paste_enabled(self, enabled):
        self.paste.append(enabled)

    def set_new_session_enabled(self, enabled):
        self.new_enabled.append(enabled)

    def focus_terminal(self):
        self.focused += 1

    def is_running(self):
        return self.running

    def defer_context_operation(self, _operation):
        return False

    def release(self):
        self.released += 1

    def begin_shutdown(self):
        pass

    def force_shutdown(self):
        pass

    def stop_monitor(self):
        pass

    def _finish_session(self):
        self.finished = True
        self.finished_callback(self)


@pytest.fixture
def terminal_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    FakeSession.instances.clear()
    window = TerminalWindow(
        SimpleNamespace(workspace=str(tmp_path)),
        FakeSession,
    )
    window.show()
    app.processEvents()
    yield window, FakeSession.instances
    window._release_sessions()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_window_starts_with_one_tab_and_routes_selected_actions(terminal_window):
    window, sessions = terminal_window
    first = sessions[0]
    assert window._current_session() is first
    assert window._tabs.count() == 1
    assert first.focused >= 1

    window._add_session()
    assert len(sessions) == 2 and window._tabs.count() == 2
    second = sessions[1]
    assert window._current_session() is second

    window._copy_selection()
    window._toggle_search()
    window._zoom_in()
    window._zoom_out()
    window._paste_clipboard()
    window._interrupt_session()
    assert second.invoked == [
        "copyClipboard", "toggleShowSearchBar", "zoomIn", "zoomOut",
        "pasteClipboard", "interrupt",
    ]
    assert second.focused >= 1


def test_window_updates_paste_status_and_tab_removal(terminal_window):
    window, sessions = terminal_window
    app = QApplication.instance()
    window._add_session()
    first, second = sessions[:2]
    app.clipboard().setText("clipboard")
    window._update_paste_action()
    assert first.paste[-1] is True and second.paste[-1] is True
    assert window._toolbar._paste.isEnabled()

    window._session_status_changed(second)
    assert window._toolbar.findChild(QLabel, "sessionStatus").text().strip().startswith("运行中")

    window._tab_close_requested(1)
    assert window._tabs.count() == 1
    assert second.released == 1
    assert window._sessions == [first]


def test_window_new_session_limit_and_owner_shutdown(terminal_window):
    window, sessions = terminal_window
    while len(window._sessions) < window.MAXIMUM_SESSIONS:
        window._add_session()
    assert len(window._sessions) == window.MAXIMUM_SESSIONS
    assert all(session.new_enabled[-1] is False for session in window._sessions)

    window._finish_session()
    assert window._current_session().finished is True
    window.show_from_owner()
    window.shutdown_from_owner()
    assert window._closing.closing is True


def test_window_release_clears_tabs_and_sessions(terminal_window):
    window, sessions = terminal_window
    window._add_session()
    window._release_sessions()
    assert window._tabs.count() == 0
    assert window._sessions == []
    assert all(session.released == 1 for session in sessions)
