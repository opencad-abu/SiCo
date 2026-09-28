"""Expand a folded chat message in one continuous, read-only document window."""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QTextCursor, QTextDocument
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton

from .chrome import SiDialog
from .code_highlight import table_safe_markdown
from .presentation import DocumentView, style_tables
from .receipts import DataReceipt


def show_event_detail(window, key):
    delivery = (window.page_presentation.delivery if window.page.reviewing is not None
                else window.binding.delivery)
    if delivery is not None:
        dialog = EventDetailDialog(window, delivery.stream, key)
        dialog.show()
        dialog.load()


class EventDetailDialog(SiDialog):
    def __init__(self, window, stream, key):
        super().__init__("完整消息", parent=window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(760, 600)
        self.window, self.stream, self.key = window, stream, key
        self.activation = window.page.activation
        self.offset = 0
        self.content_format = None
        self.render_parts = []
        self.active = True
        self.receipt = DataReceipt(self)
        self.continuation = QTimer(self)
        self.continuation.setSingleShot(True)
        self.continuation.timeout.connect(self.load)
        layout = self.body_layout
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        self.heading = QLabel("完整消息")
        self.heading.setTextFormat(Qt.PlainText)
        self.heading.setWordWrap(True)
        font = self.heading.font()
        font.setBold(True)
        self.heading.setFont(font)
        layout.addWidget(self.heading)
        self.view = DocumentView()
        self.view.setOpenLinks(False)
        self.view.setOpenExternalLinks(False)
        self.view.document().setDocumentMargin(12)
        layout.addWidget(self.view, 1)
        bar = QHBoxLayout()
        self.status = QLabel("正在展开完整消息…")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        bar.addWidget(self.status, 1)
        self.retry = QPushButton("重试")
        self.retry.hide()
        self.retry.clicked.connect(lambda _checked=False: self.load())
        bar.addWidget(self.retry)
        self.copy = QPushButton("复制全文")
        self.copy.setEnabled(False)
        self.copy.clicked.connect(
            lambda _checked=False: QApplication.clipboard().setText(self.view.toPlainText()))
        bar.addWidget(self.copy)
        close = QPushButton("关闭")
        close.clicked.connect(lambda _checked=False: self.close())
        bar.addWidget(close)
        layout.addLayout(bar)
        self.finished.connect(lambda *_args: self.stop())

    def current(self):
        if not self.active:
            return False
        if (self.window.page.activation != self.activation or self.window.lifecycle.closing):
            self.close()
            return False
        return True

    def stop(self):
        self.active = False
        self.continuation.stop()
        self.receipt.clear()
        self.render_parts.clear()

    def load(self):
        if not self.current():
            return
        self.retry.hide()
        self.view.set_busy(True)
        self.status.setText("正在展开完整消息…")
        try:
            future = self.window.api.event_detail(self.stream, self.key, self.offset)
            self.receipt.watch(future, self.receive, self.failed)
        except (ValueError, RuntimeError) as exc:
            self.failed(exc)

    def receive(self, page):
        if not self.current():
            return
        total, end, text = page["total"], page["next"], page["text"]
        content_format = (page.get("format", "plain"), page.get("body_chars", 0), total)
        if (page["offset"] != self.offset or not self.offset <= end <= total
                or len(text) != end - self.offset or (end == self.offset and end < total)
                or (self.content_format is not None and content_format != self.content_format)):
            raise ValueError("消息内容读取不连续，请重新打开")
        self.content_format = content_format
        self.heading.setText(page.get("title", "完整消息"))
        if content_format[0] in {"markdown", "html"}:
            self.render_parts.append(text)
        else:
            position = self.view.verticalScrollBar().value()
            cursor = QTextCursor(self.view.document())
            cursor.movePosition(QTextCursor.End)
            cursor.insertText(text)
            self.view.verticalScrollBar().setValue(position)
        self.offset = end
        if end < total:
            self.status.setText(f"正在展开完整消息… {end * 100 // total}%")
            self.continuation.start(0)
        else:
            self.finish_content()

    def finish_content(self):
        if self.content_format[0] in {"markdown", "html"}:
            content = "".join(self.render_parts)
            body_chars = self.content_format[1]
            if self.content_format[0] == "html":
                self.view.setHtml(content)
            else:
                self.view.document().setMarkdown(table_safe_markdown(content[:body_chars]),
                    QTextDocument.MarkdownFeatures(
                        QTextDocument.MarkdownDialectGitHub | QTextDocument.MarkdownNoHTML))
                if content[body_chars:]:
                    cursor = QTextCursor(self.view.document())
                    cursor.movePosition(QTextCursor.End)
                    cursor.insertBlock()
                    cursor.insertHtml(content[body_chars:])
            style_tables(self.view.document())
            self.view.verticalScrollBar().setValue(0)
            self.render_parts.clear()
        self.view.set_busy(False)
        self.status.setText("已展开完整消息" if self.offset else "这条消息没有正文")
        self.copy.setEnabled(self.offset > 0)

    def failed(self, exc):
        if not self.current():
            return
        self.view.set_busy(False)
        self.status.setText("消息尚未完整读取：" + str(exc))
        self.retry.show()
