"""Shared lifecycle for one single-purpose thread-control dialog."""

import uuid

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QLabel, QPushButton, QSizePolicy, QVBoxLayout

from .chrome import SiDialog
from .receipts import DataReceipt


class ThreadControlDialog(SiDialog):
    def __init__(self, window, title):
        super().__init__(title, window)
        self.window = window
        self.frontend = window.api
        self.controller = window.page.session
        self.closed = False
        self.activation, self.context = (
            window.page.activation, window.presentation.displayed_context)
        self.scope, self.snapshot = None, {}
        self.receipt = DataReceipt(self)
        self.resize(560, 460)
        self.setMinimumSize(400, 360)
        layout = self.content_layout()
        layout.setContentsMargins(12, 10, 12, 12)
        self.summary, self.hint = QLabel(), QLabel()
        for label in (self.summary, self.hint):
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            layout.addWidget(label)
        self.summary.setMaximumHeight(64)
        self.body = QVBoxLayout()
        layout.addLayout(self.body, 1)
        self.buttons = []
        self.build()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.reload)
        self.timer.start()
        self.reload()

    def build(self):
        raise NotImplementedError

    def render_page(self, snapshot, initial):
        raise NotImplementedError

    def button(self, layout, text, action):
        button = QPushButton(text)
        button.clicked.connect(lambda _checked=False: self.send(action()))
        layout.addWidget(button)
        self.buttons.append(button)
        return button

    def current(self):
        return (not self.closed and self.window.page.session == self.controller
                and self.frontend.owns(self.controller)
                and self.window.page.activation == self.activation
                and self.window.presentation.displayed_context == self.context
                and self.window.page.reviewing is None and not self.window.lifecycle.closing)

    def command(self, name, *args, **kwargs):
        return self.frontend.command(self.controller, name, *args, **kwargs)

    def reload(self):
        if self.receipt.pending is not None:
            return
        if not self.current():
            self.fail(ValueError("会话或来源已变化，请重新打开操作窗口"))
            self.timer.stop()
            return
        try:
            future = self.command("thread_status")
            self.receipt.watch(future, self.render, self.fail)
        except (ValueError, RuntimeError) as exc:
            self.fail(exc)

    def render(self, snapshot):
        if not self.current():
            return
        initial = self.scope is None
        self.scope, self.snapshot = snapshot["scope"], snapshot
        operation = snapshot.get("operation")
        if operation:
            self.hint.setText({"executing": "操作执行中", "completed": "操作已完成",
                "cancelled": "操作已停止", "failed": "操作失败",
                "needs_reconcile": "操作结果待核对，不会自动重发"}.get(
                    operation["status"], operation["status"]))
        self.render_page(snapshot, initial)

    def send(self, action):
        if not self.current() or self.scope is None:
            self.fail(ValueError("会话或来源已变化，操作未发送"))
            return
        self.receipt.clear()
        for button in self.buttons:
            button.setEnabled(False)
        try:
            future = self.command("thread_operation",
                action, scope=self.scope, request_id=uuid.uuid4().hex)
            self.receipt.watch(future, self.accepted, self.fail)
        except (ValueError, RuntimeError) as exc:
            self.fail(exc)

    def accepted(self, _request):
        if self.current():
            self.hint.setText("操作已接收")
            self.reload()

    def fail(self, exc):
        self.timer.stop()
        if self.current():
            self.hint.setText(str(exc))
        else:
            self.hint.setText("会话或来源已变化，请重新打开操作窗口")
        for button in self.buttons:
            button.setEnabled(False)

    def done(self, result):
        self.closed = True
        self.timer.stop()
        self.receipt.clear()
        super().done(result)

    def closeEvent(self, event):
        self.closed = True
        self.timer.stop()
        self.receipt.clear()
        super().closeEvent(event)
