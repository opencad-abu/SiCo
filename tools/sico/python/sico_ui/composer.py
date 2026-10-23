"""Standalone PyQt quick composer with native input-method support."""

from __future__ import annotations

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from sico.core.cad_ref import object_reference_text

from .ask_objects import AskObjectWindow
from .chrome import SiDialog
from .input import SendOnReturnEdit
from .object_mentions import ObjectMentionController
from .receipts import DataReceipt
from .wheel import WheelRouter


def skill_screen_point(initial):
    """Read the optional global point captured by the owning SKILL process."""
    value = initial.get("screen_point")
    if not isinstance(value, dict):
        return None
    x, y = value.get("x"), value.get("y")
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in (x, y)):
        return None
    if abs(x) > 1_000_000 or abs(y) > 1_000_000:
        return None
    return QPoint(x, y)


class QuickComposer(SiDialog):
    def __init__(self, initial, submit_text, query_input=None, object_request=None):
        super().__init__(title="Ask Silicon Copilot")
        self.submit_text = submit_text
        self.query_input = query_input
        self._ask_window = None
        self.object_mentions = None
        self.unconfirmed = False
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
        self.input.setPlaceholderText(
            "输入需求，@ 引用当前窗口对象，Enter 发送，Shift+Enter 换行…"
        )
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
        # 主提交动作使用窗口主题里的红棕强调色填充。
        self.submit_button.setProperty("bottomAction", True)
        layout.addLayout(buttons)
        self.submit_button.clicked.connect(self.submit)
        self.cancel.clicked.connect(self.reject)
        self.input.submitted.connect(self.submit)
        if object_request is not None and isinstance(initial.get("context"), dict):
            self._ask_window = AskObjectWindow(
                self, self.input, initial["context"], object_request
            )
            self.object_mentions = ObjectMentionController(self._ask_window)
            self._ask_window.object_mentions = self.object_mentions
        self.wheel_router = WheelRouter(self, [self.input])
        self.wheel_router.install()
        # 尺寸定下来后立刻摆位，显示时不再跳动。新 SKILL 传鼠标点；
        # 旧消息或采集失败时保留原窗口摆位。
        point = skill_screen_point(initial)
        if point is None:
            self.center_on_anchor()
        else:
            self.center_on_point(point)
        self._launch_centered = True
        if initial.get("unconfirmed"):
            self.input.setPlainText(initial["draft_text"])
            self.receive({"kind": "unknown", "message": "原请求结果待核对，请查询原请求后继续。"})

    def restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def submit(self, _checked=False):
        if self.pending or not self.host_alive:
            return
        if self.unconfirmed:
            if self.query_input is not None:
                self.pending = True
                self.submit_button.setEnabled(False)
                self.status.setText("正在查询原请求…")
                try:
                    future = self.query_input()
                    if future is not None:
                        self._send_receipt.watch(future, lambda _result: None, self._query_failed)
                except (OSError, ValueError):
                    self._query_failed(None)
            return
        QApplication.inputMethod().commit()
        text = self.input.toPlainText()
        if not text.strip():
            self.status.setText("请输入需求，最多 16000 个字符。")
            return
        if self.object_mentions is not None:
            text += object_reference_text(self.input.object_inputs())
        if len(text) > 16000 or "\0" in text:
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

    def _query_failed(self, _exc):
        if not self.finished:
            self.receive({"kind": "unknown", "message": "查询未确认原请求结果；文字已保留，可再次查询。"})

    def receive(self, message):
        kind = message.get("kind")
        if kind == "show":
            self.restore()
        elif kind == "accepted":
            if not self.pending and not self.unconfirmed:
                raise ValueError("Unexpected acceptance")
            self.finished = True
            self.accept()
        elif kind == "rejected":
            self.unconfirmed = False
            self.submit_button.setText("发送")
            self.pending = False
            self.input.setReadOnly(False)
            self.submit_button.setEnabled(True)
            self.cancel.setEnabled(True)
            self.status.setText(message["message"])
            # A delayed failure updates the draft without stealing focus.
        elif kind == "unknown":
            self.unconfirmed = True
            self.submit_button.setText("查询原请求")
            self.pending = False
            self.input.setReadOnly(False)
            self.cancel.setEnabled(True)
            self.status.setText(message["message"])
            self.submit_button.setEnabled(self.query_input is not None)
            # Unknown means that durable admission still needs a query; it is
            # never presented as a confirmed rejection or retried here.
        elif kind == "shutdown":
            self.finished = True
            self.reject()
        elif kind == "objects":
            if self._ask_window is not None:
                self._ask_window.receive(message.get("message"))
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
        if self._ask_window is not None:
            self._ask_window.close()
            self._ask_window = None
        self._send_receipt.clear()
        self.wheel_router.remove()
        super().done(result)

    def closeEvent(self, event):
        if self.pending and not self.finished:
            event.ignore()
        else:
            self.finished = True
            if self._ask_window is not None:
                self._ask_window.close()
                self._ask_window = None
            self._send_receipt.clear()
            self.wheel_router.remove()
            event.accept()
