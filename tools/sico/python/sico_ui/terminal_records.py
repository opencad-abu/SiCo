"""Read-only terminal command records; never starts or controls a shell."""

import base64
import binascii
import re

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .presentation import event_time
from .receipts import DataReceipt

READ_BYTES = 16384
MAX_BLOCKS = 20000
LIVE_MS = 300
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[@-_]")
PHASES = {"accepted": "排队", "running": "运行中", "unknown": "未知",
          "exited": "已结束", "not_started": "未启动"}


def phase_text(row):
    text = PHASES.get(row.get("phase"), str(row.get("phase") or "未知"))
    if row.get("phase") == "exited":
        status = row.get("exit_code")
        if status is None and row.get("signal") is not None:
            return text + " · 信号 " + str(row["signal"])
        if status is not None:
            return text + " · " + str(status)
    if row.get("cancel_requested"):
        return text + " · 已请求取消"
    return text


def output_text(page):
    parts = []
    for part in page.get("fragments", ()):
        try:
            data = base64.b64decode(part.get("data", ""), validate=True)
        except (binascii.Error, ValueError, TypeError):
            parts.append("[输出片段编码无效]\n")
            continue
        text = data.decode("utf-8", errors="replace")
        stream = part.get("stream")
        if stream and stream != "pty":
            text = "[" + stream + "]\n" + text
        parts.append(text)
    return display_text("".join(parts))


def display_text(text):
    """Best-effort PTY display: strip controls, apply CR overwrite and backspace."""
    line, rendered = [], []
    for char in ANSI.sub("", text):
        if char == "\r":
            line = []
        elif char == "\n":
            rendered.append("".join(line) + "\n")
            line = []
        elif char == "\b":
            if line:
                line.pop()
        elif char == "\t" or char >= " ":
            line.append(char)
    rendered.append("".join(line))
    return "".join(rendered)


class TerminalRecordsPanel(QWidget):
    """Bounded command list and live output for one session terminal."""

    def __init__(self, api, session, parent=None):
        super().__init__(parent)
        self.setObjectName("terminalRecords")
        self.api, self.session = api, session
        self._loaded = False
        self.receipt = DataReceipt(self)
        self.next_after = None
        self.next_offset = 0
        self.selected = None
        self.input_owned = False
        self.live = QTimer(self)
        self.live.setInterval(LIVE_MS)
        self.live.timeout.connect(self.poll_live)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.hide()
        split = QSplitter(Qt.Vertical)
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["时间", "状态", "命令"])
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        self.list.header().setStretchLastSection(True)
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.list.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumBlockCount(MAX_BLOCKS)
        self.detail.setPlaceholderText("选择一条命令查看已记录输出")
        self.list.currentItemChanged.connect(self.select)
        split.addWidget(self.list)
        split.addWidget(self.detail)
        split.setSizes([220, 300])
        layout.addWidget(split, 1)
        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setMaxLength(4096)
        self.input.setPlaceholderText("输入到当前 PTY 命令；不保存明文")
        self.input.setToolTip("输入只发送到当前命令 stdin；命令记录只保存 owner 和字节数")
        self.take = QPushButton("取得输入权")
        self.send_input = QPushButton("发送输入")
        self.cancel_command = QPushButton("取消命令")
        self.input.setEnabled(False)
        self.take.setEnabled(False)
        self.send_input.setEnabled(False)
        self.cancel_command.setEnabled(False)
        self.take.clicked.connect(self.take_input)
        self.send_input.clicked.connect(self.send)
        self.cancel_command.clicked.connect(self.cancel)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.take)
        input_row.addWidget(self.send_input)
        input_row.addWidget(self.cancel_command)
        layout.addLayout(input_row)
        buttons = QHBoxLayout()
        self.refresh = QPushButton("刷新")
        self.next = QPushButton("下一页")
        self.more = QPushButton("读取更多输出")
        self.next.setEnabled(False)
        self.more.setEnabled(False)
        self.refresh.clicked.connect(self.reload)
        self.next.clicked.connect(self.next_page)
        self.more.clicked.connect(self.read_more)
        buttons.addWidget(self.refresh)
        buttons.addWidget(self.next)
        buttons.addStretch(1)
        buttons.addWidget(self.more)
        layout.addLayout(buttons)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            QTimer.singleShot(0, self.reload)
        elif self.selected is not None and self.live.isActive() is False:
            row = self.list.currentItem().data(0, Qt.UserRole) if self.list.currentItem() else None
            if isinstance(row, dict) and row.get("phase") == "running" and row.get("mode") == "pty":
                self.live.start()

    def hideEvent(self, event):
        self.live.stop()
        super().hideEvent(event)

    def _command(self, name, **kwargs):
        return self.api.command(self.session, name, **kwargs)

    def reload(self):
        self._loaded = True
        self.live.stop()
        self.load_history(None)

    def next_page(self):
        if self.next_after is not None:
            self.load_history(self.next_after)

    def load_history(self, after):
        kwargs = {} if after is None else {"after": after, "limit": 20}
        try:
            future = self._command("terminal_history", **kwargs)
        except Exception as exc:
            self.failed(exc)
            return
        self.receipt.watch(future, lambda page: self.render_history(page, after),
                           self.failed)

    def render_history(self, page, after):
        rows = list(page.get("commands", ()))
        if after is None:
            self.live.stop()
            self.list.clear()
            self.detail.clear()
            self.selected = None
            self.input_owned = False
            self.input.clear()
        for row in rows:
            item = QTreeWidgetItem([
                event_time(row.get("timestamp")), phase_text(row),
                row.get("command") or "(未记录命令文本)",
            ])
            item.setData(0, Qt.UserRole, row)
            item.setToolTip(2, row.get("command") or "")
            self.list.addTopLevelItem(item)
        self.next_after = page.get("next_after")
        self.next.setEnabled(self.next_after is not None)
        total = self.list.topLevelItemCount()
        self.status.setText(
            ("已加载 %d 条命令" % total) if total else "本会话还没有命令终端记录")
        if rows and self.list.currentItem() is None:
            self.list.setCurrentItem(self.list.topLevelItem(total - 1))

    def select(self, item, _previous=None):
        if item is None:
            self.selected = None
            self.detail.clear()
            self.more.setEnabled(False)
            self.input_owned = False
            self.input.clear()
            self._controls(None)
            return
        row = item.data(0, Qt.UserRole)
        if not isinstance(row, dict) or not isinstance(row.get("ref"), dict):
            return
        self.selected = row["ref"]
        self.next_offset = 0
        self.input_owned = False
        self.input.clear()
        self._controls(row)
        self.detail.clear()
        self.more.setEnabled(False)
        live = row.get("phase") == "running" and row.get("mode") == "pty"
        if live:
            self.live.start()
        self.load_output(0, live=live)

    def load_output(self, offset, live=False):
        if self.selected is None:
            return
        if live and self.receipt.pending is not None:
            return
        try:
            future = self._command(
                "terminal_read", command=self.selected, offset=offset, limit=READ_BYTES)
        except Exception as exc:
            self.failed(exc)
            return
        self.receipt.watch(future, lambda page: self.render_output(page, live), self.failed)

    def render_output(self, page, live=False):
        text = output_text(page)
        if text:
            cursor = self.detail.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertText(text)
            self.detail.setTextCursor(cursor)
        elif self.detail.document().isEmpty():
            self.detail.setPlainText("该命令没有已记录输出。")
        self.next_offset = page.get("next_offset", self.next_offset)
        row = page.get("command")
        if isinstance(row, dict):
            self._update_row(row)
        final = isinstance(row, dict) and row.get("phase") in (
            "exited", "unknown", "not_started")
        self.more.setEnabled(bool(page.get("has_more")) and not live)
        if live:
            if final:
                self.live.stop()
                self.status.setText("命令已结束；输出已读取到当前提交位置")
                self._controls(row)
            elif page.get("has_more"):
                QTimer.singleShot(0, self.poll_live)

    def _update_row(self, row):
        item = self.list.currentItem()
        if item is None or not isinstance(row, dict):
            return
        current = item.data(0, Qt.UserRole)
        if isinstance(current, dict):
            row = {**current, **row}
        item.setData(0, Qt.UserRole, row)
        item.setText(1, phase_text(row))
        item.setText(2, row.get("command") or item.text(2))
        self._controls(row)

    def poll_live(self):
        if (not self.isVisible() or self.selected is None
                or self.receipt.pending is not None):
            return
        self.load_output(self.next_offset, live=True)

    def read_more(self):
        self.live.stop()
        self.load_output(self.next_offset)

    def _controls(self, row):
        active = (isinstance(row, dict) and not row.get("cancel_requested")
                  and row.get("phase") in ("accepted", "running", "unknown"))
        live_pty = (isinstance(row, dict) and row.get("phase") == "running"
                    and row.get("mode") == "pty")
        self.input.setEnabled(live_pty)
        self.take.setEnabled(live_pty and not self.input_owned)
        self.send_input.setEnabled(live_pty and self.input_owned)
        self.cancel_command.setEnabled(active)

    def _run(self, name, success, **kwargs):
        if self.selected is None:
            return
        try:
            future = self._command(name, command=self.selected, **kwargs)
        except Exception as exc:
            self.failed(exc)
            return
        self.receipt.watch(future, success, self.failed)

    def take_input(self):
        self._run("terminal_take_input", self.input_taken, owner="user")

    def input_taken(self, _snapshot):
        self.input_owned = True
        self._controls(self.list.currentItem().data(0, Qt.UserRole)
                       if self.list.currentItem() else None)
        self.status.setText("已取得当前命令的输入权")

    def send(self):
        text = self.input.text()
        if not text or self.selected is None:
            return
        data = base64.b64encode(text.encode("utf-8")).decode("ascii")
        self._run("terminal_write_input", self.input_sent, owner="user", data=data)

    def input_sent(self, _snapshot):
        self.input.clear()
        self.status.setText("输入已发送；记录只保存 owner 和字节数")

    def cancel(self):
        self._run("terminal_cancel", self.cancelled)

    def cancelled(self, _snapshot):
        self.status.setText("已请求取消当前命令；等待命令结束")
        item = self.list.currentItem()
        row = item.data(0, Qt.UserRole) if item else None
        if isinstance(row, dict):
            row = {**row, "cancel_requested": True}
        self._controls(row)

    def failed(self, error):
        self.live.stop()
        self.status.setText("终端记录暂不可读：" + str(error))
        self.next.setEnabled(False)
        self.more.setEnabled(False)
