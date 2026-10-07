"""Nonblocking HUP, kill and release sequence for terminal window sessions."""

from __future__ import annotations

from PyQt5.QtCore import QTimer


class WindowShutdown:
    GRACE_MS = 1200
    SETTLE_MS = 100

    def __init__(self, parent, sessions, release_sessions, close_window):
        self._sessions = sessions
        self._release_sessions = release_sessions
        self._close_window = close_window
        self.active = False
        self._grace = QTimer(parent)
        self._grace.setSingleShot(True)
        self._grace.timeout.connect(self._force)
        self._settle = QTimer(parent)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._finish)

    def stop(self):
        self._grace.stop()
        self._settle.stop()

    def begin(self):
        if self.active:
            return
        self.active = True
        for session in self._sessions():
            session.begin_shutdown()
        self._grace.start(self.GRACE_MS)

    def _force(self):
        for session in self._sessions():
            session.force_shutdown()
        self._settle.start(self.SETTLE_MS)

    def _finish(self):
        self._release_sessions()
        self.active = False
        self._close_window()
