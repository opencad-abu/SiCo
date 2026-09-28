"""Keep this window's write authority on the current session, without a menu.

一个工作区只有一个 SiCo 窗口，所以写权限不需要用户选择：没人持有（available）就获取，
上一位控制者已断开（recoverable/detached，窗口崩溃或被强杀）就自动接管；只有别的活窗口
仍持有（occupied）时保持只读。控制权变更由服务写进会话记录，事后可核对。
"""

import time

from PyQt5.QtCore import QObject, Qt, QTimer
from PyQt5.QtWidgets import QLabel

from .receipts import DataReceipt


class ServiceControl(QObject):
    """Owns the authority label and the automatic acquire/resume/takeover of one window."""

    LABELS = {
        "available": "正在准备会话…", "recoverable": "正在恢复会话连接…",
        "detached": "正在恢复会话连接…", "occupied": "其他窗口正在使用此会话",
        "owned": "", "unknown": "正在确认会话连接…",
    }
    ACTIONS = {"available": "acquire", "recoverable": "resume", "detached": "takeover"}

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.label = QLabel()
        self.label.setTextFormat(Qt.PlainText)
        window.add_service_status(self.label)
        self.receipt = DataReceipt(self)
        self._displayed = None
        self._due = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(250)
        self.refresh()

    def refresh(self):
        window = self.window
        if window.lifecycle.closing:
            self.receipt.clear()
            self.timer.stop()
            return
        token = window.page.session
        control = window.api.control
        try:
            control.refresh(token)
        except (ValueError, RuntimeError):
            pass
        view = control.view(token)
        state = view.state if view else "unknown"
        writable = window.api.can_control(token)
        if writable or window.page.reviewing is not None or window.api.is_closing(token):
            window.session_notices.clear("control")
        if (token, writable) != self._displayed:
            self._displayed = token, writable
            if window.presentation.state is not None:
                window.update_session(window.presentation.state)
        ready = (not window.page.opening and window.page.reviewing is None and view is not None
                 and not window.api.is_closing(token))
        if not writable and ready and self.receipt.pending is None:
            if self.ACTIONS.get(state) is not None and time.monotonic() >= self._due:
                self.request(self.ACTIONS[state])
        self.label.setText("" if writable else self.LABELS.get(state, self.LABELS["unknown"]))

    def request(self, action):
        """Ask the service for one authority change; the page stays the original page."""
        window = self.window
        if window.lifecycle.closing or window.page.reviewing is not None or window.page.opening:
            return
        token, activation = window.page.session, window.page.activation

        def current():
            return (not window.lifecycle.closing and window.page.session == token
                    and window.page.activation == activation and window.page.reviewing is None)

        def complete(_view):
            if current():
                window.session_notices.clear("control")
                self.refresh()
                window.refresh_status()

        def failed(exc):
            self._due = time.monotonic() + 1
            if current():
                from sico.service.service_errors import is_definite_refusal

                title = ("会话暂不可操作" if is_definite_refusal(exc)
                         else "会话连接尚未确认")
                window.session_notices.set("control", title, str(exc))

        try:
            self.receipt.watch(window.api.control.ensure(token), complete, failed)
        except (ValueError, RuntimeError) as exc:
            failed(exc)
