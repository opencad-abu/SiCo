"""方片字形：整块方片，或方片里挖出箭头、勾、列表符号的一族按钮图标。"""

from __future__ import annotations

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QPainterPath, QTransform

from .glyph_paint import ACTION_GLYPH_SIZE, filled_icon, joined, stroked
from .theme import ACCENT, STOP

# 方片占画布的比例和圆角；挖空符号同样按画布比例给。
PLATE_RATIO = 0.90
PLATE_RADIUS = 0.10
ARROW_STEM = 0.085
ARROW_HEAD = 0.36
ARROW_SPAN = (0.15, 0.71)


def _plate(size):
    side = PLATE_RATIO * size
    path = QPainterPath()
    path.addRoundedRect(QRectF((size - side) / 2, (size - side) / 2, side, side),
                        PLATE_RADIUS * side, PLATE_RADIUS * side)
    return path


def _arrow_mark(size, direction):
    """向下箭头；左右两个方向靠整体旋转得到，形状只有一份。"""
    if direction not in (Qt.DownArrow, Qt.LeftArrow, Qt.RightArrow):
        raise ValueError("plate arrow must be Qt.DownArrow, Qt.LeftArrow or Qt.RightArrow")
    center, top, tip = size / 2.0, ARROW_SPAN[0] * size, ARROW_SPAN[1] * size
    half_stem, half_head = ARROW_STEM * size / 2, ARROW_HEAD * size / 2
    middle = tip - ARROW_HEAD * size / 2
    mark = QPainterPath(QPointF(center - half_stem, top))
    mark.lineTo(center + half_stem, top)
    mark.lineTo(center + half_stem, middle)
    mark.lineTo(center + half_head, middle)
    mark.lineTo(center, tip)
    mark.lineTo(center - half_head, middle)
    mark.lineTo(center - half_stem, middle)
    mark.closeSubpath()
    turn = {Qt.DownArrow: 0, Qt.LeftArrow: 90, Qt.RightArrow: -90}[direction]
    if not turn:
        return mark
    transform = QTransform()
    transform.translate(center, center)
    transform.rotate(turn)
    transform.translate(-center, -center)
    return transform.map(mark)


def _check_mark(size):
    """勾：一短一长两段折线。"""
    path = QPainterPath(QPointF(0.24 * size, 0.52 * size))
    path.lineTo(0.43 * size, 0.71 * size)
    path.lineTo(0.78 * size, 0.30 * size)
    return stroked(path, 0.14 * size)


def _text_mark(size):
    """列表：两条短横，代表打开详情。"""
    first = QPainterPath(QPointF(0.24 * size, 0.38 * size))
    first.lineTo(0.78 * size, 0.38 * size)
    second = QPainterPath(QPointF(0.24 * size, 0.62 * size))
    second.lineTo(0.60 * size, 0.62 * size)
    return joined(stroked(first, 0.13 * size), stroked(second, 0.13 * size))


def plate_arrow_icon(direction, size=ACTION_GLYPH_SIZE, color=ACCENT):
    """方片箭头：回到最新（下）、上一页（左）、下一页（右）共用。"""
    return filled_icon(_plate(size).subtracted(_arrow_mark(size, direction)), size, color)


def plate_check_icon(size=ACTION_GLYPH_SIZE, color=ACCENT):
    """方片勾：设为默认这类确认动作，取代 share 里的 check 位图。"""
    return filled_icon(_plate(size).subtracted(_check_mark(size)), size, color)


def plate_text_icon(size=ACTION_GLYPH_SIZE, color=ACCENT):
    """方片列表：打开详情，取代 share 里的 text 位图。"""
    return filled_icon(_plate(size).subtracted(_text_mark(size)), size, color)


def stop_icon(size=ACTION_GLYPH_SIZE, color=STOP):
    """停止图标：整块红色方片，红色只留给这个最需要看见的动作。"""
    return filled_icon(_plate(size), size, color)
