"""Scoped lifecycle for a single turn-draft editor dialog."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialogButtonBox, QLabel

from .chrome import SiDialog
from .receipt_scope import ReceiptScope
from .receipts import DataReceipt


class TurnDialog(SiDialog):
    def __init__(self, title, window):
        super().__init__(title, window)
        self.window = window
        self.scope = ReceiptScope.capture(window.page, window.presentation.submission_context)
        self.closed = False
        self.catalog = None
        self.setWindowModality(Qt.WindowModal)
        self.body_layout.setContentsMargins(12, 12, 12, 12)
        self.hint = QLabel("正在读取可用选项…")
        self.hint.setTextFormat(Qt.PlainText)
        self.hint.setWordWrap(True)
        self.body_layout.addWidget(self.hint)
        self.receipt = DataReceipt(self)
        self.finished.connect(self.finish)

    def load(self):
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        self.body_layout.addWidget(buttons)
        self.ok = buttons.button(QDialogButtonBox.Ok)
        self.ok.setEnabled(False)
        try:
            self.receipt.watch(self.window.api.command(self.scope.handle, "turn_input_status"),
                               self.loaded, self.failed)
        except (ValueError, RuntimeError) as exc:
            self.failed(exc)

    def current(self):
        window = self.window
        return (not self.closed and window.page.reviewing is None and not window.page.opening
                and self.scope.current(window.page, window.presentation.submission_context,
                                       window.api, window.lifecycle.closing))

    def loaded(self, status):
        if not self.current():
            self.reject()
            return
        self.catalog = status
        self.populate()
        self.ok.setEnabled(True)
        self.hint.setText("任务执行中 · 使用最近可用选项" if status["busy"] else "")
        self.window.turn_composer.use_catalog(status)

    def failed(self, exc):
        if self.current():
            self.hint.setText(str(exc))
        else:
            self.reject()
        return False

    def finish(self, _result):
        self.closed = True
        self.receipt.clear()
