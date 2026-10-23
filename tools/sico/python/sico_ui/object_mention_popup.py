"""Read-only popup used by the @ object picker."""

from __future__ import annotations

from PyQt5.QtCore import QPoint, Qt, pyqtSignal
from PyQt5.QtWidgets import QFrame, QLabel, QListWidget, QListWidgetItem, QVBoxLayout


class ObjectMentionPopup(QFrame):
    loadMoreRequested = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("objectMentionPopup")
        self.setFrameShape(QFrame.StyledPanel)
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        self.title = QLabel("对象引用")
        self.title.setTextFormat(Qt.PlainText)
        layout.addWidget(self.title)
        self.rows = QListWidget()
        self.rows.setFocusPolicy(Qt.NoFocus)
        self.rows.setUniformItemSizes(True)
        self.rows.setMaximumHeight(220)
        layout.addWidget(self.rows, 1)
        self.rows.verticalScrollBar().valueChanged.connect(self._scroll_changed)
        self.hint = QLabel()
        self.hint.setTextFormat(Qt.PlainText)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.setMinimumWidth(430)
        self.resize(430, 300)
        self.hide()

    def _scroll_changed(self, value):
        bar = self.rows.verticalScrollBar()
        if bar.maximum() > 0 and value >= bar.maximum() - 2:
            self.loadMoreRequested.emit()

    def show_loading(self, text="正在读取当前选择…"):
        self.rows.clear()
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        self.rows.addItem(item)
        self.hint.setText("↑/↓ 选择，Enter 插入，Esc 关闭")
        self._show()

    def show_error(self, text):
        self.rows.clear()
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        self.rows.addItem(item)
        self.hint.setText("当前没有可插入的对象")
        self._show()

    def set_items(self, items):
        self.rows.clear()
        for row in items:
            item = QListWidgetItem(row["label"] + " · " + row["detail"])
            item.setData(Qt.UserRole, row)
            self.rows.addItem(item)
        if items:
            self.rows.setCurrentRow(0)
            self.hint.setText("↑/↓ 选择，Enter 插入，Esc 关闭")
        else:
            item = QListWidgetItem("没有匹配的对象")
            item.setFlags(Qt.NoItemFlags)
            self.rows.addItem(item)
            self.hint.setText("继续输入可过滤对象")
        self._show()

    def set_hint(self, text):
        self.hint.setText(text)

    def current_payload(self):
        item = self.rows.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def move_selection(self, delta):
        if self.rows.count() <= 0:
            return
        row = self.rows.currentRow()
        if row < 0:
            row = 0
        self.rows.setCurrentRow(max(0, min(self.rows.count() - 1, row + delta)))

    def _show(self):
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        input_widget = getattr(parent, "input", None)
        if input_widget is not None:
            point = input_widget.mapTo(parent, QPoint(0, 0))
            y = point.y() - self.height() - 4
            if y < 0:
                y = point.y() + input_widget.height() + 4
            self.move(max(4, point.x()), max(4, y))
        self.show()
        self.raise_()


__all__ = ["ObjectMentionPopup"]
