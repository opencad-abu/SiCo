"""Read-only popup used by the @ object picker."""

from __future__ import annotations

from PyQt5.QtCore import QPoint, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

MARGIN = 4


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
        self.hint.setWordWrap(False)
        layout.addWidget(self.hint)
        self.setMinimumWidth(430)
        self.resize(430, 300)
        self.hide()

    def _scroll_changed(self, value):
        bar = self.rows.verticalScrollBar()
        if bar.maximum() > 0 and value >= bar.maximum() - 2:
            self.loadMoreRequested.emit()

    def show_loading(self, text="正在读取当前选择…"):
        self.set_title("对象引用")
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

    def set_title(self, text):
        self.title.setText(text)

    def show_scope(self, scope, *, count=0, selection_count=0, object_count=0,
                   object_total=0, truncated=False, query=""):
        if scope == "selection":
            self.set_title("对象引用 · 当前选择")
            if count:
                self.set_hint(
                    f"当前选择 {selection_count} 个对象 · "
                    "↑/↓ 选择，Enter 插入，Esc 关闭")
            else:
                self.set_hint("当前选择没有匹配项 · 清空选择后可浏览当前窗口")
            return
        if scope == "window":
            self.set_title("对象引用 · 当前窗口")
            if truncated:
                self.set_hint(
                    f"已加载 {object_count} / {object_total} 个对象 · 滚动加载更多")
            elif object_total == 0 and not query:
                self.set_hint("当前窗口没有可引用的器件、网络或引脚")
            else:
                self.set_hint(
                    "器件 / 网络 / 引脚 · ↑/↓ 选择，Enter 插入，Esc 关闭")
            return
        self.set_title("对象引用")

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
        input_widget = getattr(parent, "input", None) if parent is not None else None
        if input_widget is None:
            return
        bounds = self._bounds(parent)
        anchor = input_widget.mapTo(parent, QPoint(0, 0))
        above_y = anchor.y() - MARGIN
        below_y = anchor.y() + input_widget.height() + MARGIN
        available_above = above_y - bounds.top()
        available_below = bounds.bottom() + 1 - below_y
        self.setMinimumWidth(min(430, bounds.width()))
        self.setMaximumWidth(bounds.width())
        self.setMaximumHeight(bounds.height())
        self.adjustSize()
        width = min(bounds.width(), max(240, self.width()))
        desired = min(self.height(), bounds.height())
        candidates = []
        if available_above > 0:
            above = min(desired, available_above)
            candidates.append((above, above_y - above))
        if available_below > 0:
            candidates.append((min(desired, available_below), below_y))
        # The body fallback overlays the input when neither side can show a
        # complete list; it still stays inside the composer.
        candidates.append((max(1, min(desired, bounds.height())), bounds.top()))
        height, y = max(candidates, key=lambda item: item[0])
        self.resize(width, height)
        y = max(bounds.top(), min(y, bounds.bottom() + 1 - height))
        x = min(max(bounds.left(), anchor.x()), bounds.right() + 1 - width)
        self.move(x, y)
        self.show()
        self.raise_()

    @staticmethod
    def _bounds(parent):
        body = getattr(parent, "body", None)
        if isinstance(body, QWidget) and body.isVisible():
            rect = body.geometry()
        else:
            rect = parent.rect()
        return rect.adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)


__all__ = ["ObjectMentionPopup"]
