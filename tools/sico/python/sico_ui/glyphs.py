"""Qt-painted window glyphs: markers the window draws itself, not share rasters.

每个字形都是一条 QPainterPath：透明画布上只填一个颜色，所以界面图标不再
依赖 share 目录里的位图。方片一族（挖空箭头/勾/列表）见 ``glyph_plates``，
绘制原语见 ``glyph_paint``。
"""

from __future__ import annotations

import math

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QPainterPath, QPolygonF

from .glyph_paint import (
    ACTION_GLYPH_SIZE,
    INPUT_GLYPH_SIZE,
    filled_icon,
    joined,
    polar,
    stroked,
    turned,
)
from .theme import ACCENT

# 标题栏/关闭这类家族字形由 cadgui 提供，这里按历史路径再导出。
from cadgui.glyphs import close_icon as close_icon
from cadgui.glyphs import cross_icon as cross_icon
from cadgui.glyphs import maximize_icon as maximize_icon
from cadgui.glyphs import minimize_icon as minimize_icon
from cadgui.glyph_paint import TITLE_GLYPH_SIZE as TITLE_GLYPH_SIZE

# 三角只占画布中间，形状比等边三角形窄一点：条状手柄里的标记要小。
TRIANGLE_FILL_RATIO = 0.62
TRIANGLE_ASPECT = 0.8
# 回形针是两个叠在一起的环：半长、半宽和线宽（画布比例），整体 45° 斜置。
PAPERCLIP_RINGS = ((0.42, 0.155, 0.095), (0.13, 0.075, 0.07))


def triangle_points(direction, size):
    """居中三角的端点；``direction`` 只接受 Qt.LeftArrow 与 Qt.RightArrow。"""
    if direction not in (Qt.LeftArrow, Qt.RightArrow):
        raise ValueError("triangle direction must be Qt.LeftArrow or Qt.RightArrow")
    height = size * TRIANGLE_FILL_RATIO
    width = height * TRIANGLE_ASPECT
    center = size / 2.0
    left, right = center - width / 2.0, center + width / 2.0
    top, bottom = center - height / 2.0, center + height / 2.0
    if direction == Qt.RightArrow:
        return QPolygonF(
            [QPointF(right, center), QPointF(left, top), QPointF(left, bottom)]
        )
    return QPolygonF(
        [QPointF(left, center), QPointF(right, top), QPointF(right, bottom)]
    )


def triangle_icon(direction, size, color=ACCENT):
    """一块实心三角作图标：默认红棕色，和窗口强调色一致。"""
    path = QPainterPath()
    path.addPolygon(triangle_points(direction, size))
    return filled_icon(path, size, color)


def _paperclip_outline(size):
    """两个叠在一起的环：外环长、内环短，都是圆角描边的回形针线圈。"""
    center = size / 2.0
    rings = []
    for half_length, half_width, width in PAPERCLIP_RINGS:
        ring = QPainterPath()
        ring.addRoundedRect(
            QRectF(center - half_width * size, center - half_length * size,
                   2 * half_width * size, 2 * half_length * size),
            half_width * size, half_width * size)
        rings.append(stroked(ring, width * size))
    return turned(joined(*rings), size, 45)


def paperclip_icon(size=INPUT_GLYPH_SIZE, color=ACCENT):
    """附件图标：Qt 自绘的两个叠置红棕回形针，取代 share 里的位图。"""
    return filled_icon(_paperclip_outline(size), size, color)


def _gear_outline(size):
    """齿顶、齿根交替取样成一条闭合轮廓，再挖掉轮毂。"""
    center = QPointF(size / 2.0, size / 2.0)
    outer, body, hub = (radius * size for radius in (0.46, 0.32, 0.13))
    path = QPainterPath()
    step = 360.0 / 8
    tip = step * 0.55
    for index in range(8):
        middle = index * step - 90.0
        arcs = ((tip, outer, middle - tip / 2), (step - tip, body, middle + tip / 2))
        for sweep, radius, start in arcs:
            for point in range(7):
                angle = math.radians(start + sweep * point / 6)
                corner = QPointF(center.x() + radius * math.cos(angle),
                                 center.y() + radius * math.sin(angle))
                if path.elementCount() == 0:
                    path.moveTo(corner)
                else:
                    path.lineTo(corner)
    path.closeSubpath()
    hub_hole = QPainterPath()
    hub_hole.addEllipse(center, hub, hub)
    return path.subtracted(hub_hole)


def gear_icon(size=INPUT_GLYPH_SIZE, color=ACCENT):
    """回合设置图标：Qt 自绘的红棕齿轮，取代 share 里的位图。"""
    return filled_icon(_gear_outline(size), size, color)


def _send_outline(size):
    """纸飞机：机头朝右，机尾收一个缺口。"""
    path = QPainterPath(QPointF(0.94 * size, 0.50 * size))
    path.lineTo(0.08 * size, 0.10 * size)
    path.lineTo(0.36 * size, 0.50 * size)
    path.lineTo(0.08 * size, 0.90 * size)
    path.closeSubpath()
    return path


def send_icon(size=ACTION_GLYPH_SIZE, color=ACCENT):
    """发送图标：Qt 自绘的红棕纸飞机，取代 share 里的 send 位图。"""
    return filled_icon(_send_outline(size), size, color)


def _back_outline(size):
    """返回箭头：指向左边的一条实心箭头。"""
    shaft, half, head = 0.13 * size, 0.33 * size, 0.46 * size
    middle = size / 2.0
    path = QPainterPath(QPointF(0.07 * size, middle))
    path.lineTo(head, middle - half)
    path.lineTo(head, middle - shaft / 2)
    path.lineTo(0.93 * size, middle - shaft / 2)
    path.lineTo(0.93 * size, middle + shaft / 2)
    path.lineTo(head, middle + shaft / 2)
    path.lineTo(head, middle + half)
    path.closeSubpath()
    return path


def back_icon(size=ACTION_GLYPH_SIZE, color=ACCENT):
    """返回图标：Qt 自绘的红棕左箭头，取代 share 里的 back 位图。"""
    return filled_icon(_back_outline(size), size, color)


def _refresh_outline(size):
    """开口圆环加一个切向箭头：刷新与重连共用一个字形。"""
    center = size / 2.0
    radius, gap, width, head = 0.30 * size, 70, 0.11 * size, 0.40 * size
    start, end = 45 + gap / 2, 45 - gap / 2
    ring = QPainterPath()
    box = QRectF(center - radius, center - radius, 2 * radius, 2 * radius)
    ring.arcMoveTo(box, start)
    ring.arcTo(box, start, -(360 - gap))
    arrow = QPainterPath(polar(center, radius, end - 30))
    arrow.lineTo(polar(center, radius + head * 0.6, end))
    arrow.lineTo(polar(center, radius - head * 0.6, end))
    arrow.closeSubpath()
    return joined(stroked(ring, width), arrow)


def refresh_icon(size=ACTION_GLYPH_SIZE, color=ACCENT):
    """刷新图标：Qt 自绘的红棕循环箭头，取代 share 里的 refresh 位图。"""
    return filled_icon(_refresh_outline(size), size, color)


def _info_outline(size):
    """信息圆：圆环里一个点和一根竖线。"""
    width = 0.11 * size
    radius = (0.5 - 0.07) * size - width / 2
    center = size / 2.0
    ring = QPainterPath()
    ring.addEllipse(QPointF(center, center), radius, radius)
    dot = QPainterPath()
    dot.addEllipse(QPointF(center, 0.32 * size), 0.055 * size, 0.055 * size)
    stem = QPainterPath(QPointF(center, 0.47 * size))
    stem.lineTo(center, 0.70 * size)
    return joined(stroked(ring, width), dot, stroked(stem, width))


def info_icon(size=ACTION_GLYPH_SIZE, color=ACCENT):
    """信息图标：Qt 自绘的红棕圆环加竖点，取代 share 里的 info 位图。"""
    return filled_icon(_info_outline(size), size, color)


def _plus_outline(size):
    """加号：新建会话这类“再来一个”的动作。"""
    arm, far, near = 0.145 * size, 0.88 * size, 0.12 * size
    middle = size / 2.0
    corners = ((middle + arm, near), (middle + arm, middle - arm), (far, middle - arm),
               (far, middle + arm), (middle + arm, middle + arm), (middle + arm, far),
               (middle - arm, far), (middle - arm, middle + arm), (near, middle + arm),
               (near, middle - arm), (middle - arm, middle - arm))
    path = QPainterPath(QPointF(middle - arm, near))
    for x, y in corners:
        path.lineTo(x, y)
    path.closeSubpath()
    return path


def plus_icon(size=ACTION_GLYPH_SIZE, color=ACCENT):
    """新建图标：Qt 自绘的红棕加号，取代 share 里的 new 位图。"""
    return filled_icon(_plus_outline(size), size, color)


def _terminal_outline(size):
    """终端窗口：外框、标题栏和命令行提示符。"""
    width = 0.09 * size
    frame = QPainterPath()
    frame.addRoundedRect(QRectF(0.12 * size, 0.16 * size, 0.76 * size, 0.68 * size),
                         0.10 * size, 0.10 * size)
    bar = QPainterPath(QPointF(0.12 * size, 0.36 * size))
    bar.lineTo(0.88 * size, 0.36 * size)
    prompt = QPainterPath(QPointF(0.28 * size, 0.48 * size))
    prompt.lineTo(0.40 * size, 0.60 * size)
    prompt.lineTo(0.28 * size, 0.72 * size)
    line = QPainterPath(QPointF(0.48 * size, 0.72 * size))
    line.lineTo(0.68 * size, 0.72 * size)
    return joined(stroked(frame, width), stroked(bar, width * 0.8),
                   stroked(prompt, width), stroked(line, width))


def terminal_icon(size=ACTION_GLYPH_SIZE, color=ACCENT):
    """终端图标：Qt 自绘的红棕命令行窗口，取代 share 里的 terminal 位图。"""
    return filled_icon(_terminal_outline(size), size, color)
