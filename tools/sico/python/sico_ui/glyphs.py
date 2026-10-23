"""Qt-painted window glyphs: markers the window draws itself, not share rasters.

每个字形都是一条 QPainterPath：透明画布上只填一个颜色，所以界面图标不再
依赖 share 目录里的位图。方片一族（挖空箭头/勾/列表）见 ``glyph_plates``，
绘制原语见 ``glyph_paint``。
"""

from __future__ import annotations

import math

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPolygonF,
)

from .glyph_paint import (
    ACTION_GLYPH_SIZE,
    INPUT_GLYPH_SIZE,
    filled_icon,
    filled_image,
    joined,
    polar,
    stroked,
    turned,
)
from .theme import ACCENT, active_palette

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


def _goal_outline(size):
    """持续目标：左侧靶环，右上短箭带尾羽指向靶心。"""
    center = QPointF(0.26 * size, 0.58 * size)
    path = QPainterPath()
    path.setFillRule(Qt.OddEvenFill)
    path.addEllipse(center, 0.24 * size, 0.24 * size)
    path.addEllipse(center, 0.14 * size, 0.14 * size)
    tail, tip = QPointF(0.76 * size, 0.22 * size), QPointF(0.48 * size, 0.42 * size)
    line = QPainterPath(tail)
    line.lineTo(tip)
    shaft = stroked(line, 0.08 * size)
    dx, dy = tail.x() - tip.x(), tail.y() - tip.y()
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    head = 0.18 * size
    arrowhead = QPainterPath(tip)
    arrowhead.lineTo(tip.x() + ux * head + px * 0.115 * size,
                     tip.y() + uy * head + py * 0.115 * size)
    arrowhead.lineTo(tip.x() + ux * head - px * 0.115 * size,
                     tip.y() + uy * head - py * 0.115 * size)
    arrowhead.closeSubpath()
    path.addPath(shaft)
    path.addPath(arrowhead)
    for sign in (-1, 1):
        feather = QPainterPath(tail)
        feather.lineTo(tail.x() - ux * 0.04 * size + px * sign * 0.13 * size,
                       tail.y() - uy * 0.04 * size + py * sign * 0.13 * size)
        path.addPath(stroked(feather, 0.07 * size))
    return path


def goal_icon(size=INPUT_GLYPH_SIZE, color=ACCENT):
    """持续目标图标：Qt 自绘的红棕靶环与带尾羽短箭。"""
    return filled_icon(_goal_outline(size), size, color)


def pen_icon(size=INPUT_GLYPH_SIZE, color=ACCENT):
    """纪要图标：斜置的钢笔，笔尖保留气孔与中缝。"""
    barrel = QPainterPath()
    barrel.addRoundedRect(QRectF(0.39 * size, 0.07 * size, 0.22 * size, 0.49 * size),
                          0.07 * size, 0.07 * size)
    band = QPainterPath()
    band.addRect(QRectF(0.36 * size, 0.58 * size, 0.28 * size, 0.06 * size))
    nib = QPainterPath(QPointF(0.40 * size, 0.66 * size))
    for x, y in ((0.60, 0.66), (0.66, 0.77), (0.50, 0.96), (0.34, 0.77)):
        nib.lineTo(x * size, y * size)
    nib.closeSubpath()
    hole = QPainterPath()
    hole.addEllipse(QPointF(0.50 * size, 0.76 * size), 0.045 * size, 0.045 * size)
    slit = QPainterPath(QPointF(0.50 * size, 0.76 * size))
    slit.lineTo(0.50 * size, 0.96 * size)
    nib = nib.subtracted(joined(hole, stroked(slit, 0.035 * size)))
    return filled_icon(turned(joined(barrel, band, nib), size, 45), size, color)


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


def rewind_image(size=16, color=ACCENT, background=None):
    """会话气泡里的回退图标：直接绘制 ⤴︎ 字形，并加浅色圆底。"""
    background = active_palette().selection if background is None else background
    canvas = size * 4
    image = QImage(canvas, canvas, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(background))
    painter.drawEllipse(QPointF(canvas / 2.0, canvas / 2.0), canvas * 0.47, canvas * 0.47)
    font = QFont("DejaVu Sans")
    if not QFontMetrics(font).inFontUcs4(0x2934):
        font = QFont()
    font.setPixelSize(int(canvas * 0.74))
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(image.rect(), Qt.AlignCenter, "\u2934\ufe0e")
    painter.end()
    return image


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


# 工具计数图标：锤子和开口扳手先在横放坐标系里各画一条，再整体斜置成 X。
# 每件工具的 x 轴都从柄尾指到头，所以锤头和扳手钳口的坐标是负的。
HAMMER_HEAD = (-0.52, -0.33, 0.35, 0.06)     # x0, x1, 头高, 圆角
HAMMER_HANDLE = (-0.46, 0.58, 0.155, 0.075)  # x0, x1, 柄粗, 圆角
WRENCH_JAW = (-0.50, -0.22, 0.39, 0.11)      # x0, x1, 钳口块高, 圆角
WRENCH_MOUTH = (-0.56, -0.34, 0.15, 0.045)   # 从钳口外缘切进去的开口
WRENCH_HANDLE = (-0.36, 0.58, 0.145, 0.07)
HAMMER_TURN = 45   # 锤子：头在左上，柄伸向右下
WRENCH_TURN = 135  # 扳手：开口在右上，柄伸向左下


def _tool_piece(size, spec):
    """横放坐标系里的一块圆角矩形：以画布中心为原点，两件工具都由这样的块拼成。"""
    x0, x1, height, radius = spec
    path = QPainterPath()
    path.addRoundedRect(QRectF(size / 2 + x0 * size, size / 2 - height * size / 2,
                               (x1 - x0) * size, height * size),
                        radius * size, radius * size)
    return path


def _hammer_wrench_outline(size):
    """交叉的锤子与扳手：锤头（左上）是实心块，扳手开口从钳口外缘（右上）切进去。"""
    hammer = joined(_tool_piece(size, HAMMER_HEAD), _tool_piece(size, HAMMER_HANDLE))
    wrench = joined(_tool_piece(size, WRENCH_JAW), _tool_piece(size, WRENCH_HANDLE))
    wrench = wrench.subtracted(_tool_piece(size, WRENCH_MOUTH))
    return joined(turned(hammer, size, HAMMER_TURN), turned(wrench, size, WRENCH_TURN))


def hammer_wrench_image(size, color=ACCENT):
    """工具计数图标：文内位图的 Qt 自绘锤子扳手，在聊天里替代“工具”二字。"""
    return filled_image(_hammer_wrench_outline(size), size, color)
