"""Standalone PyQt quick composer with native input-method support."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .chrome import SiDialog
from .input import SendOnReturnEdit
from .receipts import DataReceipt
from .wheel import WheelRouter


class QuickComposer(SiDialog):
    def __init__(self, initial, submit_text):
        super().__init__(title="Ask Silicon Copilot")
        self.submit_text = submit_text
        self._send_receipt = DataReceipt(self)
        self.pending = False
        self.finished = False
        self.host_alive = True
        self.resize(600, 285 + self.title_bar.height())
        layout = QVBoxLayout()
        self.content_layout().addLayout(layout)
        self.destination = QLabel("自动选择会话：同一 lib/cell 共用会话，无关联设计新建会话")
        self.source = QLabel(initial["source"])
        for label in (self.destination, self.source):
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        self.input = SendOnReturnEdit()
        self.input.setObjectName("quickRequest")
        self.input.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.input.setPlaceholderText("输入需求，Enter 发送，Shift+Enter 换行…")
        layout.addWidget(self.input, 1)
        self.status = QLabel("发送后在 Silicon Copilot 会话中继续")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel = QPushButton("取消")
        self.submit_button = QPushButton("发送")
        for button in (self.cancel, self.submit_button):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.submit_button.clicked.connect(self.submit)
        self.cancel.clicked.connect(self.reject)
        self.input.submitted.connect(self.submit)
        self.wheel_router = WheelRouter(self, [self.input])
        self.wheel_router.install()
        # 尺寸定下来后立刻按“SiCo 中心”摆位，显示时不再跳动。
        self.center_on_anchor()
        self._launch_centered = True

    def restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def submit(self, _checked=False):
        if self.pending or not self.host_alive:
            return
        QApplication.inputMethod().commit()
        text = self.input.toPlainText()
        if not text.strip() or len(text) > 16000 or "\0" in text:
            self.status.setText("请输入需求，最多 16000 个字符。")
            return
        self.pending = True
        self.input.setReadOnly(True)
        self.submit_button.setEnabled(False)
        self.cancel.setEnabled(False)
        self.status.setText("正在转交，等待会话确认…")
        try:
            future = self.submit_text(text)
            if future is not None:
                self._send_receipt.watch(future, lambda _result: None, self._send_failed)
        except (OSError, ValueError):
            self.disconnected()

    def _send_failed(self, exc):
        if self.finished:
            return
        if isinstance(exc, ValueError):
            self.receive({"kind": "rejected", "message": "请输入需求，最多 16000 个字符。"})
        else:
            self.disconnected()

    def receive(self, message):
        kind = message.get("kind")
        if kind == "show":
            self.restore()
        elif kind == "accepted":
            if not self.pending:
                raise ValueError("Unexpected acceptance")
            self.finished = True
            self.accept()
        elif kind == "rejected":
            self.pending = False
            self.input.setReadOnly(False)
            self.submit_button.setEnabled(True)
            self.cancel.setEnabled(True)
            self.status.setText(message["message"])
            # A delayed failure updates the draft without stealing focus.
        elif kind == "unknown":
            self.pending = False
            self.input.setReadOnly(False)
            self.submit_button.setEnabled(True)
            self.cancel.setEnabled(True)
            self.status.setText(message["message"])
            # Unknown means that durable admission still needs a query; it is
            # never presented as a confirmed rejection or retried here.
        elif kind == "shutdown":
            self.finished = True
            self.reject()
        else:
            raise ValueError("Invalid composer control")

    def disconnected(self):
        self.host_alive = False
        self.pending = False
        self.input.setReadOnly(False)
        self.submit_button.setEnabled(False)
        self.cancel.setEnabled(True)
        self.status.setText("Virtuoso 连接已结束；文字已保留，可复制后关闭窗口。")
        # Host loss is passive; preserve the user's current desktop/focus.

    def reject(self, _checked=False):
        if not self.pending or self.finished:
            self.finished = True
            super().reject()

    def done(self, result):
        self._send_receipt.clear()
        self.wheel_router.remove()
        super().done(result)

    def closeEvent(self, event):
        if self.pending and not self.finished:
            event.ignore()
        else:
            self.finished = True
            self._send_receipt.clear()
            self.wheel_router.remove()
            event.accept()
