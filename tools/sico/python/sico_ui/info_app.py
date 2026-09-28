"""One nonblocking, Chinese notification presenter per desktop window."""

from collections import deque
from dataclasses import dataclass

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QVBoxLayout,
)

from .chrome import SiDialog


@dataclass(frozen=True)
class Notice:
    level: str
    title: str
    message: str


class _InfoDialog(SiDialog):
    """通知对话框：和主界面同一套无边框 chrome，只保留关闭按钮。"""

    ICONS = {"错误": QStyle.SP_MessageBoxCritical, "警告": QStyle.SP_MessageBoxWarning,
             "信息": QStyle.SP_MessageBoxInformation, "提示": QStyle.SP_MessageBoxInformation}

    def __init__(self, parent):
        super().__init__("SiCo", parent, ("close",))
        self.setObjectName("infoDialog")
        self.setWindowModality(Qt.NonModal)
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(14)
        self.body_layout.addLayout(layout)
        heading = QHBoxLayout()
        self.icon = QLabel()
        heading.addWidget(self.icon, 0, Qt.AlignTop)
        self.heading = QLabel()
        self.heading.setObjectName("infoHeading")
        self.heading.setTextFormat(Qt.PlainText)
        self.heading.setWordWrap(True)
        font = self.heading.font()
        font.setBold(True)
        self.heading.setFont(font)
        heading.addWidget(self.heading, 1)
        layout.addLayout(heading)
        self.body = QPlainTextEdit()
        self.body.setObjectName("infoMessage")
        self.body.setReadOnly(True)
        self.body.setMinimumSize(360, 140)
        layout.addWidget(self.body, 1)
        actions = QHBoxLayout()
        self.count = QLabel()
        self.count.setObjectName("infoCount")
        actions.addWidget(self.count, 1)
        copy = QPushButton("复制信息")
        copy.setObjectName("infoCopy")
        copy.clicked.connect(lambda _checked: self.copy())
        actions.addWidget(copy)
        self.confirm = QPushButton("知道了")
        self.confirm.setObjectName("infoConfirm")
        self.confirm.setDefault(True)
        self.confirm.clicked.connect(self.accept)
        actions.addWidget(self.confirm)
        layout.addLayout(actions)
        self.resize(600, 340)

    def present(self, notice, repeats, remaining):
        title = "SiCo · " + notice.level
        self.setWindowTitle(title)
        self.title_bar.title.setText(title)
        self.heading.setText(notice.level + " · " + notice.title)
        self.icon.setPixmap(self.style().standardIcon(self.ICONS[notice.level]).pixmap(32, 32))
        self.body.setPlainText(notice.message)
        self.counter(repeats, remaining)
        self.show()
        self.raise_()
        self.activateWindow()

    def counter(self, repeats, remaining):
        self.count.setText("；".join(filter(None, (
            f"相同消息 {repeats} 次" if repeats > 1 else "",
            f"还有 {remaining} 条消息" if remaining else ""))))
        self.confirm.setText("下一条" if remaining else "知道了")

    def copy(self):
        QApplication.clipboard().setText(self.heading.text() + "\n\n" + self.body.toPlainText())


class InfoApp(QObject):
    """Explicit notices only; callers keep progress and passive state in their own views.

    Delivery is marshalled to Qt. Repeated pending notices coalesce; acknowledging
    one never submits, retries, cancels, or otherwise changes a service operation.
    """

    _posted = pyqtSignal(object)

    def __init__(self, parent, *, closing=lambda: False):
        super().__init__(parent)
        self._closing = closing
        self._queue = deque()
        self._counts = {}
        self._current = None
        self._dialog = None
        self._stopped = False
        self._advance = QTimer(self)
        self._advance.setSingleShot(True)
        self._advance.timeout.connect(self._show_next)
        self._posted.connect(self._receive)

    def error(self, title, message):
        self._posted.emit(Notice("错误", str(title), str(message)))

    def warning(self, title, message):
        self._posted.emit(Notice("警告", str(title), str(message)))

    def info(self, title, message):
        self._posted.emit(Notice("信息", str(title), str(message)))

    def notice(self, title, message):
        self._posted.emit(Notice("提示", str(title), str(message)))

    def _receive(self, notice):
        if self._stopped or self._closing():
            return
        if notice in self._counts:
            self._counts[notice] += 1
        else:
            self._counts[notice] = 1
            self._queue.append(notice)
        if self._current is None:
            self._show_next()
        else:
            self._dialog.counter(self._counts[self._current], len(self._queue))

    def _show_next(self):
        if self._stopped or self._closing() or self._current is not None or not self._queue:
            return
        if self._dialog is None:
            self._dialog = _InfoDialog(self.parent())
            self._dialog.finished.connect(self._acknowledged)
        self._current = self._queue.popleft()
        self._dialog.present(self._current, self._counts[self._current], len(self._queue))

    def _acknowledged(self, _result):
        self._counts.pop(self._current, None)
        self._current = None
        if not self._stopped:
            self._advance.start(0)

    def close(self):
        self._stopped = True
        self._advance.stop()
        self._queue.clear()
        self._counts.clear()
        if self._dialog is not None:
            self._dialog.close()
