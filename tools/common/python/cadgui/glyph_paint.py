"""字形绘制原语：画布尺寸，以及把一条路径变成图标要用的几个动作。"""

from __future__ import annotations

import math

from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPainterPathStroker, QPixmap, QTransform

# 字形画布：输入区 20px，会话底部与面板按钮 18px，标题栏按钮 14px。
INPUT_GLYPH_SIZE = 20
ACTION_GLYPH_SIZE = 18
TITLE_GLYPH_SIZE = 14


def filled_icon(path, size, color):
    """透明画布上只填色的矢量图标：颜色由调用方给，底图不来自任何资源文件。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def stroked(path, width, *, cap=Qt.RoundCap, join=Qt.RoundJoin):
    """把一条线变成可填充的轮廓：线宽同样按画布比例给。"""
    stroker = QPainterPathStroker()
    stroker.setWidth(width)
    stroker.setCapStyle(cap)
    stroker.setJoinStyle(join)
    return stroker.createStroke(path)


def joined(*paths):
    """多个子形状合成一条路径：重叠处不互相抵消。"""
    combined = QPainterPath()
    combined.setFillRule(Qt.WindingFill)
    for path in paths:
        combined.addPath(path)
    return combined


def turned(path, size, degrees):
    """绕画布中心旋转一个字形：回形针和方片箭头都靠它换方向。"""
    center = size / 2.0
    transform = QTransform()
    transform.translate(center, center)
    transform.rotate(degrees)
    transform.translate(-center, -center)
    return transform.map(path)


def polar(center, radius, degrees):
    """极坐标取点：0° 在正右，逆时针为正。"""
    angle = math.radians(degrees)
    return QPointF(center + radius * math.cos(angle), center - radius * math.sin(angle))
