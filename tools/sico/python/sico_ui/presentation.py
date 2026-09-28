"""Passive Qt document with shared bounded display formatters."""

from PyQt5.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QTextCharFormat,
    QTextCursor,
    QTextTable,
    QTextTableFormat,
)
from PyQt5.QtWidgets import QTextBrowser

from sico.service.display import (
    FIELD_LABELS,
    data_html,
    event_time,
    is_recommended,
    plain_recommendation,
    recommended_label,
    target_label,
    token_amount,
)
from .theme import ACCENT, TABLE_BORDER

__all__ = (
    "BusyCursor", "DocumentView", "FIELD_LABELS", "data_html", "event_time",
    "is_recommended", "plain_recommendation", "recommended_label", "target_label",
    "token_amount", "document_tables", "style_tables",
)


def document_tables(document, *, nested=False):
    """收集文档里的表格；``nested=True`` 只取嵌在别的框架/表格里的那些。

    会话里的消息气泡本身也是表格，气泡里的 markdown/工具结果表格才是内容表格。
    """
    found = []

    def walk(frame, depth):
        for child in frame.childFrames():
            if isinstance(child, QTextTable) and (not nested or depth > 0):
                found.append(child)
            walk(child, depth + 1)

    walk(document.rootFrame(), 0)
    return found


def style_tables(document, *, nested=False):
    """表格统一边框与表头：1px #888888 实线格线，表头红棕色加粗。

    详情页、会话、工具结果、记录详情共用；字段表（data_html）没有表头，
    首行保持正文样式。
    """
    brush = QBrush(QColor(TABLE_BORDER))
    for table in document_tables(document, nested=nested):
        fmt = table.format().toTableFormat()
        # Qt 会把表格边与相邻单元格边各取整后叠在一起，线厚只能是偶数：
        # 1 → 2px、2 → 4px、3 → 6px；按用户反馈取 1.0（实测 2px）。
        fmt.setBorder(1.0)
        fmt.setBorderStyle(QTextTableFormat.BorderStyle_Solid)
        fmt.setBorderBrush(brush)
        # markdown 表格默认 cellSpacing=2、cellPadding=0：分别归零/补齐，
        # 格线才不会变成“两条 1px 夹白缝”，文字也不会贴着框线。
        fmt.setCellSpacing(0.0)
        fmt.setCellPadding(5.0)
        table.setFormat(fmt)
        style_table_header(document, table)


def style_table_header(document, table):
    """首行本来就是粗体（markdown 表 / 工具表）的表格，表头改红棕色加粗。"""

    if table.rows() < 1 or table.columns() < 1:
        return False
    weights = [table.cellAt(0, column).firstCursorPosition().charFormat().fontWeight()
               for column in range(table.columns())]
    if min(weights) < QFont.Bold:
        return False
    header = QTextCharFormat()
    header.setForeground(QBrush(QColor(ACCENT)))
    header.setFontWeight(QFont.Bold)
    for column in range(table.columns()):
        cell = table.cellAt(0, column)
        cursor = QTextCursor(document)
        cursor.setPosition(cell.firstCursorPosition().position())
        cursor.setPosition(cell.lastCursorPosition().position(), QTextCursor.KeepAnchor)
        cursor.mergeCharFormat(header)
    return True


class BusyCursor(QObject):
    """Busy pointer that only follows the widget currently loading data.

    加载数据、读取或继续历史记录期间 busy 只出现在对应的控件里；模型思考与
    工具执行不改变光标，气泡里的工具结果始终可以点击。文字控件悬停时会自己
    改写光标（链接小手、I 形光标），所以进入/移动后要重新压上 busy；
    ``owns_cursor`` 让控件保留自己的光标（例如气泡里的链接仍是小手）。
    """

    def __init__(self, widget, owns_cursor=None):
        super().__init__(widget)
        self.owns_cursor = owns_cursor
        self.target = widget.viewport() if hasattr(widget, "viewport") else widget
        self.busy = False
        self._restore = None
        self.target.installEventFilter(self)

    def set(self, busy):
        """Apply or drop the busy pointer for this widget."""
        busy = bool(busy)
        if busy == self.busy:
            return
        self.busy = busy
        if busy:
            self._restore = self.target.cursor()
            if self.target.underMouse():
                self.target.setCursor(Qt.BusyCursor)
        elif self._restore is not None:
            self.target.setCursor(self._restore)
            self._restore = None

    def eventFilter(self, _obj, event):
        if self.busy and event.type() in (QEvent.Enter, QEvent.MouseMove):
            if self.owns_cursor is None or not self.owns_cursor(event):
                self.target.setCursor(Qt.BusyCursor)
        return False


class DocumentView(QTextBrowser):
    # Double-click is a separate gesture from the single-click anchorClicked.
    linkDoubleClicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Links are routed through anchorClicked; a failed navigation would
        # otherwise blank the whole transcript.
        self.setOpenLinks(False)
        # 链接保留小手光标；其余区域在页面加载时才显示 busy。
        self._busy = BusyCursor(
            self, owns_cursor=lambda event: bool(self.anchor_text_at(event.pos())),
        )

    def set_busy(self, busy):
        """Busy pointer for this view only; other widgets keep their own cursors."""
        self._busy.set(busy)

    def anchor_text_at(self, position):
        """Anchor href under a point, including the empty tail of its line."""
        cursor = self.cursorForPosition(position)
        href = cursor.charFormat().anchorHref()
        block = cursor.block()
        if href or not block.text() or cursor.position() != block.position() + block.length() - 1:
            return href
        tail = QTextCursor(cursor)
        tail.setPosition(cursor.position() - 1)
        return tail.charFormat().anchorHref()

    def mouseDoubleClickEvent(self, event):
        href = self.anchor_text_at(event.pos())
        if href:
            self.linkDoubleClicked.emit(href)
        super().mouseDoubleClickEvent(event)

    def loadResource(self, resource_type, url):
        # Rich text may contain model/tool-controlled local or remote image URLs.
        return None
