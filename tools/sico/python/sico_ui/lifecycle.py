"""Respond to the owning Virtuoso process even inside a modal startup dialog."""

from __future__ import annotations

import queue
import time

from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtWidgets import QApplication

from sico.service.diagnostics import diagnostics
from .ask_activation import AskActivation


class DesktopControl(QObject):
    def __init__(self, reader, *, notices=None, registration=None, startup_shutdown=None,
                 shutdown_guard=None):
        super().__init__()
        self.reader = reader
        self.notices = notices
        self.registration = registration
        self.startup_shutdown = startup_shutdown
        self.shutdown_guard = shutdown_guard
        self.startup_window = None
        self.window = None
        self.closing = False
        self._notice_state = ""
        self._last_show = float("-inf")
        self.pending = []
        self.pending_attach = None
        self.pending_asks = []
        self.ask_overflow = False
        self.ask_activation = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)

    def poll(self):
        if self.reader is not None and self.reader.closed.is_set():
            self.shutdown()
            return
        self._poll_notices()
        if self.reader is None:
            return
        for _ in range(20):
            try:
                message = self.reader.lines.get_nowait()
            except queue.Empty:
                return
            if not isinstance(message, dict):
                self.shutdown()
                return
            kind = message.get("kind")
            if kind == "shutdown":
                self.shutdown()
                return
            if self.registration is not None:
                register, self.registration = self.registration, None
                register(message)
                continue
            if kind == "show" and not self.closing:
                now = time.monotonic()
                if now - self._last_show < 1.0:
                    continue
                self._last_show = now
                if self.window:
                    self.window.restore()
                else:
                    modal = QApplication.activeModalWidget() or self.startup_window
                    if modal:
                        modal.raise_()
                        modal.activateWindow()
            elif kind == "submit" and not self.closing:
                if self.window and hasattr(self.window, "quick_input"):
                    self.window.quick_input.accept(message)
                elif len(self.pending) < 24:
                    self.pending.append(message)
                else:
                    self.shutdown()
            elif kind == "attach" and not self.closing:
                session_id = message.get("session_id")
                if (not isinstance(session_id, str) or len(session_id) != 32
                        or any(char not in "0123456789abcdef" for char in session_id)):
                    self.shutdown()
                    return
                if "ask_sequence" in message:
                    if self.window is not None:
                        self._activate(session_id, message["input_id"], message["ask_sequence"])
                    elif len(self.pending_asks) < 64:
                        self.pending_asks.append(message)
                    else:
                        self.ask_overflow = True
                        diagnostics.submit(__name__, "Ask activation queue_full; admission unchanged")
                elif self.window is not None:
                    self._activate(session_id)
                else:
                    self.pending_attach = session_id
            elif kind != "show":
                self.shutdown()
                return

    def attach(self, window):
        self.window = window
        self._notice_state = ""
        self._poll_notices()
        for message in self.pending:
            window.quick_input.accept(message)
        self.pending.clear()
        if self.pending_attach is not None:
            session_id, self.pending_attach = self.pending_attach, None
            self._activate(session_id)

        for message in self.pending_asks:
            self._activate(message["session_id"], message["input_id"], message["ask_sequence"])
        self.pending_asks.clear()
        if self.ask_overflow:
            self.ask_overflow = False
            if self.ask_activation is None:
                self.ask_activation = AskActivation(window)
            self.ask_activation._report("Ask 激活队列已满；任务接收状态不变，请在会话中查看。")

    def _activate(self, session_id, input_id=None, ask_sequence=None):
        if self.ask_activation is None or self.ask_activation.window is not self.window:
            if self.ask_activation is not None:
                self.ask_activation.close()
            self.ask_activation = AskActivation(self.window)
        self.ask_activation.activate(session_id, input_id, ask_sequence)

    def _poll_notices(self):
        if self.notices is None:
            return
        stalled = getattr(self.notices, "stalled", None)
        state = ("failed" if self.notices.failed.is_set() else
                 "stalled" if stalled is not None and stalled.is_set() else "")
        if state == self._notice_state:
            return
        self._notice_state = state
        # Notification failure is not proof that the host or session ended.
        # Surface the loss of receipts/actions while control remains connected.
        diagnostics.submit(__name__, "Desktop notice channel state: " + (state or "ready"))
        if self.window is not None:
            self.window.actions.update_host_notices(state)

    def shutdown(self):
        if self.closing:
            return
        if self.shutdown_guard is not None:
            self.shutdown_guard.arm()
        self.closing = True
        if self.ask_activation is not None:
            self.ask_activation.close()
        if self.window:
            self.window.request_quit()
        else:
            modal = QApplication.activeModalWidget()
            if modal:
                modal.close()
            if self.startup_shutdown is not None:
                self.startup_shutdown()

    def close(self):
        self.timer.stop()
        if self.ask_activation is not None:
            self.ask_activation.close()
        if self.reader is not None:
            self.reader.close(timeout=0)
