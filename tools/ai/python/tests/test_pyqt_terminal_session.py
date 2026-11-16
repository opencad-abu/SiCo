"""TerminalSession owns shell status and resource shutdown without a real terminal."""

from __future__ import annotations

import os
import signal
from types import SimpleNamespace

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QScrollBar, QVBoxLayout, QWidget

import terminal_fixtures  # noqa: F401  # installs the PyQt package path
from cad_ai_terminal import terminal_session as module


class Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in self.slots:
            slot(*args)


class Terminal:
    def __init__(self, pid=42):
        self.pid = pid
        self.finished = Signal()
        self.termKeyPressed = Signal()
        self.receivedData = Signal()
        self.calls = []
        self.started = 0
        self.closed = 0

    def getShellPID(self):
        return self.pid

    def screenColumnsCount(self):
        return 80

    def screenLinesCount(self):
        return 24

    def historyLinesCount(self):
        return 100

    def pasteClipboard(self):
        self.calls.append("paste")

    def copyClipboard(self):
        self.calls.append("copy")

    def scrollToEnd(self):
        self.calls.append("scroll")

    def setSelectionStart(self, row, column):
        self.calls.append(("selection-start", row, column))

    def setSelectionEnd(self, row, column):
        self.calls.append(("selection-end", row, column))

    def setFocus(self, reason):
        self.calls.append(("focus", reason))

    def sendText(self, text):
        self.calls.append(("send", text))

    def startShellProgram(self):
        self.started += 1

    def invoke_marker(self):
        self.calls.append("invoke")

    def close(self):
        self.closed += 1

    def deleteLater(self):
        self.calls.append("delete")


class Context:
    def __init__(self, *_args, is_closing, **_kwargs):
        self.is_closing = is_closing
        self.new_enabled = None
        self.paste_enabled = None
        self.deferred = []
        self.stopped = 0
        self.released = 0

    def set_new_session_enabled(self, enabled):
        self.new_enabled = enabled

    def set_paste_enabled(self, enabled):
        self.paste_enabled = enabled

    def defer_context_operation(self, operation):
        self.deferred.append(operation)
        return False

    def stop(self):
        self.stopped += 1

    def release(self):
        self.released += 1


@pytest.fixture
def terminal_class(monkeypatch):
    terminal = Terminal()
    monkeypatch.setattr(module, "create_terminal_widget", lambda *_args, **_kwargs: terminal)
    monkeypatch.setattr(module, "sanitize_shell_environment", lambda: None)
    monkeypatch.setattr("cad_ai_terminal.session_context.TerminalContextMenu", Context)
    qapp = QApplication.instance() or QApplication([])
    qt = {
        "QApplication": QApplication,
        "QTimer": __import__("PyQt5.QtCore", fromlist=["QTimer"]).QTimer,
        "QVBoxLayout": QVBoxLayout,
        "QWidget": QWidget,
        "Qt": Qt,
        "QScrollBar": QScrollBar,
    }
    cls = module.create_terminal_session_class(
        qt=qt,
        qtermwidget_type=object,
        options=SimpleNamespace(program="bash", arguments=["-l"], workspace="."),
        resource_path=lambda _name: None,
        append_test_output=lambda *_args: None,
        fixed_pitch_terminal_font=lambda *_args: None,
        schedule_scroll_restore=lambda *_args: None,
    )
    return cls, terminal, qapp


def test_session_starts_tracks_shell_and_routes_actions(terminal_class):
    cls, terminal, _app = terminal_class
    finished = []
    statuses = []
    parent = QWidget()
    session = cls(3, lambda: None, finished.append, lambda item: statuses.append(item.status),
                  parent)
    try:
        assert (session.number, session.title) == (3, "会话 3")
        assert session.terminal is terminal
        assert session.status[0] == "运行中"
        assert terminal.started == 1
        assert session.is_running() and session.shell_pid() == 42

        session.invoke("invoke_marker")
        session.copy_selection()
        session.paste_clipboard()
        session.select_all()
        session.interrupt()
        session.focus_terminal()
        assert ("send", "\x03") in terminal.calls
        assert ("selection-end", 123, 79) in terminal.calls
        assert ("focus", Qt.OtherFocusReason) in terminal.calls

        session.set_new_session_enabled(False)
        session.set_paste_enabled(True)
        assert session._context.new_enabled is False
        assert session._context.paste_enabled is True
        assert session.defer_context_operation(lambda: None) is False
        assert len(session._context.deferred) == 1
    finally:
        session.release()
        parent.deleteLater()
    assert session.finished and session.terminal is None
    assert terminal.closed == 1
    assert statuses


def test_session_poll_and_finish_are_idempotent(terminal_class):
    cls, terminal, _app = terminal_class
    finished = []
    statuses = []
    parent = QWidget()
    session = cls(1, lambda: None, finished.append, lambda item: statuses.append(item.status),
                  parent)
    try:
        terminal.pid = 0
        session._poll_session()
        assert session.finished is True
        assert finished == [session]
        assert session.status[0] == "已退出"
        session._poll_session()
        assert finished == [session]
        session._finish_session()
        assert finished == [session]
    finally:
        session.release()
        parent.deleteLater()


def test_session_shutdown_signals_and_releases_owned_terminal(terminal_class, monkeypatch):
    cls, terminal, _app = terminal_class
    sent = []
    monkeypatch.setattr(module.os, "kill", lambda pid, number: sent.append((pid, number)))
    parent = QWidget()
    session = cls(2, lambda: None, lambda _item: None, lambda _item: None, parent)
    try:
        session.begin_shutdown()
        assert session._closing is True
        assert sent == [(42, signal.SIGHUP)]
        session.force_shutdown()
        assert sent == [(42, signal.SIGHUP), (42, signal.SIGKILL)]
    finally:
        session.release()
        parent.deleteLater()
    assert session._context is None
    assert session._monitor.isActive() is False
