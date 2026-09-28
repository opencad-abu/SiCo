"""The closing menu row and its mouse interaction."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QMenu, QStyle, QWidget

from .glyphs import cross_icon
from .theme import SELECTION, STOP


class ClosingMenuEntry(QWidget):
    """Red, bold closing row that lines up with the native menu items.

    Qt colours a whole menu, never one item, so this row paints itself: the
    same left inset, icon column and gap the style uses, then red bold text.
    """

    ICON = 16
    GAP = 6
    INSET = 2

    def __init__(self, title, callback, parent=None):
        super().__init__(parent)
        self._callback = callback
        self._hover = False
        self.setObjectName("closingEntry")
        style = self.style() if parent is None else parent.style()
        left = style.pixelMetric(QStyle.PM_DefaultFrameWidth, None, parent) + self.INSET
        layout = QHBoxLayout(self)
        layout.setContentsMargins(left, 0, 8, 0)
        layout.setSpacing(self.GAP)
        mark = QLabel()
        mark.setObjectName("closingEntryIcon")
        mark.setFixedWidth(self.ICON)
        mark.setPixmap(cross_icon(self.ICON, STOP).pixmap(self.ICON, self.ICON))
        caption = QLabel(title)
        caption.setObjectName("closingEntryText")
        for widget in (mark, caption):
            # 点击落到整行上，由行统一触发并收起菜单。
            widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(widget, 0, Qt.AlignVCenter)
        layout.addStretch(1)
        self.setMinimumHeight(self.fontMetrics().height() + 8)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        if self._hover:
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(SELECTION))
        super().paintEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            menu = self.parentWidget()
            if isinstance(menu, QMenu):
                menu.close()
            self._callback()
            return
        super().mouseReleaseEvent(event)
