"""Qt-painted window glyphs shared by every family window: title bar and close marks."""

from __future__ import annotations

from PyQt5.QtCore import QPointF, QRectF
from PyQt5.QtGui import QPainterPath

from .glyph_paint import TITLE_GLYPH_SIZE, filled_icon, stroked
from .theme import ACCENT, STOP


def _cross_outline(size):
    """叉：两条对角短线，关闭窗口和结束任务共用。"""
    path = QPainterPath(QPointF(0.20 * size, 0.20 * size))
    path.lineTo(0.80 * size, 0.80 * size)
    path.moveTo(0.80 * size, 0.20 * size)
    path.lineTo(0.20 * size, 0.80 * size)
    return stroked(path, 0.17 * size)


def cross_icon(size=TITLE_GLYPH_SIZE, color=ACCENT):
    """叉图标：Qt 自绘的红棕叉，取代 share 里的 uncheck 位图。"""
    return filled_icon(_cross_outline(size), size, color)


def close_icon(size=TITLE_GLYPH_SIZE, color=STOP):
    """关闭图标：和停止同一支红色，退出动作一眼可见。"""
    return cross_icon(size, color)


def _minimize_outline(size):
    """最小化：标题栏上的一根短横。"""
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.16 * size, 0.43 * size, 0.68 * size, 0.14 * size),
                        0.07 * size, 0.07 * size)
    return path


def minimize_icon(size=TITLE_GLYPH_SIZE, color=ACCENT):
    """最小化图标：Qt 自绘的红棕短横，取代 share 里的 min 位图。"""
    return filled_icon(_minimize_outline(size), size, color)


def _maximize_outline(size):
    """最大化：标题栏上的方框。"""
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.22 * size, 0.22 * size, 0.56 * size, 0.56 * size),
                        0.10 * size, 0.10 * size)
    return stroked(path, 0.13 * size)


def maximize_icon(size=TITLE_GLYPH_SIZE, color=ACCENT):
    """最大化图标：Qt 自绘的红棕方框，取代 share 里的 max 位图。"""
    return filled_icon(_maximize_outline(size), size, color)
