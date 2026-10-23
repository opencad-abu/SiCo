"""Read-only context occupancy ring inside the composer's square control cell."""

from collections.abc import Mapping

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QWidget

from .theme import active_palette


class ContextUsage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("contextUsage")
        self.setAccessibleName("上下文使用情况")
        self._usage = None
        self.set_usage(None)

    def set_usage(self, value):
        used = value.get("used") if isinstance(value, Mapping) else None
        capacity = value.get("capacity") if isinstance(value, Mapping) else None
        usage = ((used, capacity) if type(used) is int and used >= 0
                 and type(capacity) is int and capacity > 0 else None)
        self._usage = usage
        if usage is None:
            tip = "上下文使用情况：暂无数据\n等待当前会话返回上下文用量和模型容量。"
        else:
            tip = (f"上下文已使用 {used / capacity:.1%}\n"
                   f"{used:,} / {capacity:,} 词元\n"
                   "最近一次模型响应的上下文占用估算；压缩后会下降。")
        if tip != self.toolTip():
            self.setToolTip(tip)
            self.setAccessibleDescription(tip)
            self.update()

    def paintEvent(self, event):
        palette = active_palette()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(palette.background))
        painter.setPen(QPen(QColor(palette.accent), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.setRenderHint(QPainter.Antialiasing)
        ring = QRectF(self.rect()).adjusted(4.5, 4.5, -4.5, -4.5)
        painter.setPen(QPen(QColor(palette.selection), 2.5))
        painter.drawEllipse(ring)
        label, color = "—", palette.muted
        if self._usage is not None:
            used, capacity = self._usage
            ratio = min(1.0, used / capacity)
            color = palette.error if ratio >= 0.9 else palette.accent
            painter.setPen(QPen(QColor(color), 2.5, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(ring, 90 * 16, -round(ratio * 360 * 16))
            label = str(round(ratio * 100)) + "%"
        font = QFont(self.font())
        font.setPixelSize(8)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(self.rect(), Qt.AlignCenter, label)
