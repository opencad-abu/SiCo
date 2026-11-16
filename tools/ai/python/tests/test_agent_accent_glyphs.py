"""界面图标全部由 Qt 自绘：红棕色为主，停止与关闭保留红色。"""

from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal
from sico_ui import glyph_plates, glyphs
from sico_ui.branding import icon as share_icon
from sico_ui.theme import ACCENT, active_palette

# 输入/发送区的方形按钮用 20px 画布，会话底部和面板按钮用 18px。
COMPOSER_GLYPH_SIZE = 20


def build_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    journal = SessionJournal(tmp_path, "accent-glyphs")
    journal.__enter__()
    loop = PythonBackend(
        ContextDemoProvider(), ToolRegistry(), journal,
        BoundContext("glyph-instance", "glyph-generation", "bound", {"valid": True}),
    )
    return app, journal, AssistantWindow(loop, persistent=True)


def glyph_image(widget, size):
    return widget.icon().pixmap(QSize(size, size)).toImage()


def ink(widget, size):
    """控件图标上可见的像素坐标集合。"""
    image = glyph_image(widget, size)
    return {(x, y) for y in range(size) for x in range(size)
            if image.pixelColor(x, y).alpha() > 128}


def colors(widget, size):
    image = glyph_image(widget, size)
    return [image.pixelColor(x, y) for y in range(size) for x in range(size)
            if image.pixelColor(x, y).alpha() > 128]


def enclosed_holes(pixels, size):
    """从画布边缘灌水后进不去的透明像素：回形针的环和齿轮的轮毂。"""
    outside = set()
    stack = [(x, y) for x in range(size) for y in (0, size - 1) if (x, y) not in pixels]
    stack += [(x, y) for y in range(size) for x in (0, size - 1) if (x, y) not in pixels]
    while stack:
        point = stack.pop()
        if point in outside or point in pixels:
            continue
        outside.add(point)
        x, y = point
        for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= neighbour[0] < size and 0 <= neighbour[1] < size:
                stack.append(neighbour)
    return sum(1 for y in range(size) for x in range(size)
               if (x, y) not in pixels and (x, y) not in outside)


def reach(pixels, size, degrees):
    """从画布中心沿一个角度能走到的最大半径：齿轮用它比较齿顶和齿根。"""
    angle = math.radians(degrees)
    distance = 0
    for step in range(size):
        point = (int(size / 2 + step * math.cos(angle)), int(size / 2 + step * math.sin(angle)))
        if point in pixels:
            distance = step
    return distance


def test_composer_icons_are_qt_painted_accent_glyphs(tmp_path):
    """输入区按钮只留图标不留文字，字形统一为红棕色 Qt 绘制。"""
    app, journal, window = build_window(tmp_path)
    try:
        composer = window.turn_composer
        assert composer.attachments.text() == ""
        assert composer.attachments.accessibleName() == "附件"
        accent = QColor(ACCENT)
        for label, control, asset, glyph, size in (
            ("附件", composer.attachments, "attachment.png", glyphs.paperclip_icon,
             COMPOSER_GLYPH_SIZE),
            ("回合设置", composer.settings, "setting.png", glyphs.gear_icon,
             COMPOSER_GLYPH_SIZE),
            ("回到最新", window.latest, "down.png", glyph_plates.plate_arrow_icon,
             glyphs.ACTION_GLYPH_SIZE),
        ):
            if glyph is glyph_plates.plate_arrow_icon:
                expected = glyph(Qt.DownArrow, size)
            else:
                expected = glyph(size)
            assert not control.icon().isNull(), label
            assert glyph_image(control, size) == expected.pixmap(QSize(size, size)).toImage()
            visible = colors(control, size)
            assert visible, label
            # 抗锯齿只改透明度：可见像素本身就是强调色。
            for pixel in visible:
                assert abs(pixel.red() - accent.red()) <= 2, label
                assert abs(pixel.green() - accent.green()) <= 2, label
                assert abs(pixel.blue() - accent.blue()) <= 2, label
            # share 位图仍在树里（其它工具用），只是这些控件不再引用它。
            assert control.icon().cacheKey() != share_icon(asset).cacheKey(), label
        assert composer.goal.accessibleName() == "持续目标"
        goal = composer.goal
        size = COMPOSER_GLYPH_SIZE
        assert glyph_image(goal, size) == glyphs.goal_icon(size).pixmap(
            QSize(size, size)).toImage()
        assert all(abs(pixel.red() - accent.red()) <= 2
                   and abs(pixel.green() - accent.green()) <= 2
                   and abs(pixel.blue() - accent.blue()) <= 2
                   for pixel in colors(goal, size))
        app.processEvents()
    finally:
        window.close()
        journal.__exit__(None, None, None)


def test_composer_goal_button_opens_the_goal_page(tmp_path, monkeypatch):
    app, journal, window = build_window(tmp_path)
    try:
        composer = window.turn_composer
        opened = []
        composer.goal.setEnabled(True)
        monkeypatch.setattr(composer, "available", lambda: True)
        monkeypatch.setattr(
            "sico_ui.thread_controls.show_thread_controls",
            lambda window, **kwargs: opened.append((window, kwargs)),
        )
        composer.goal.click()
        assert opened == [(window, {"page": "goal"})]
        app.processEvents()
    finally:
        window.close()
        journal.__exit__(None, None, None)


def test_glyphs_keep_the_paperclip_gear_and_plate_shapes(tmp_path):
    """形状仍是叠置的回形针、8 齿齿轮和方片挖空图标：换色不换语义。"""
    app, journal, window = build_window(tmp_path)
    try:
        composer = window.turn_composer
        size = COMPOSER_GLYPH_SIZE
        paperclip = ink(composer.attachments, size)
        # 45° 斜置：右上、左下有线，另外两角空。
        assert (13, 3) in paperclip and (3, 14) in paperclip
        assert (2, 2) not in paperclip and (17, 17) not in paperclip
        # 两个叠在一起的环围出封闭空隙，而不是一根线或一个实心块。
        assert enclosed_holes(paperclip, size) > 20

        gear = ink(composer.settings, size)
        # 8 个齿在 45° 间隔上：齿顶明显长于齿根，中心是掏空的轮毂。
        for index in range(8):
            tip = reach(gear, size, -90 + 45 * index)
            root = reach(gear, size, -90 + 45 * index + 22.5)
            assert tip > root * 1.15, index
        assert (size // 2, size // 2) not in gear
        assert len([x for x in range(size) if (x, size // 2) not in gear]) >= 3

        goal = ink(composer.goal, size)
        # 左侧是留孔的靶环；右上短箭带尾羽，不再像带把手的放大镜。
        assert enclosed_holes(goal, size) >= 3
        assert any(x >= size - 5 and y <= 5 for x, y in goal)

        size = glyphs.ACTION_GLYPH_SIZE
        arrow = ink(window.latest, size)
        # 方片挖箭头：方片上下两端是实的，挖空的箭头被方片围住，四角留在外面。
        assert (9, 1) in arrow and (9, 16) in arrow
        assert (8, 11) not in arrow and (9, 11) not in arrow
        assert enclosed_holes(arrow, size) > 0
        assert (0, 0) not in arrow and (size - 1, size - 1) not in arrow
        app.processEvents()
    finally:
        window.close()
        journal.__exit__(None, None, None)


def test_hammer_wrench_counter_mark_carries_both_tools():
    """工具计数图标是自绘的锤子扳手：锤头、开口、两条柄都在一副红棕色画布上。"""
    QApplication.instance() or QApplication([])
    size = 48
    image = glyphs.hammer_wrench_image(size)
    accent = QColor(ACCENT)
    pixels = {(x, y) for y in range(size) for x in range(size)
              if image.pixelColor(x, y).alpha() > 128}
    assert pixels
    for x, y in pixels:
        pixel = image.pixelColor(x, y)
        assert abs(pixel.red() - accent.red()) <= 2, (x, y)
        assert abs(pixel.green() - accent.green()) <= 2, (x, y)
        assert abs(pixel.blue() - accent.blue()) <= 2, (x, y)
    # 锤头在左上、开口扳手在右上；两者的头顶之间留出空隙，柄穿过中心伸向下角。
    assert (10, 6) in pixels and (7, 10) in pixels
    assert (24, 10) not in pixels and (10, 24) not in pixels
    assert (24, 24) in pixels
    assert (10, 38) in pixels and (38, 38) in pixels
    # 扳手钳口从外缘切进去：开口中心是空的。
    assert (40, 8) not in pixels and (41, 8) not in pixels
    # 画布四角留边：图标不贴死画布边界。
    assert (0, 0) not in pixels and (size - 1, size - 1) not in pixels


def test_stop_keeps_the_alert_red_while_the_rest_of_the_row_is_accent(tmp_path):
    """停止按钮同样自绘，但保留红色做唯一的强提醒；其余按钮都是红棕色。"""
    app, journal, window = build_window(tmp_path)
    try:
        size = glyphs.ACTION_GLYPH_SIZE
        assert glyph_image(window.stop, size) == glyph_plates.stop_icon(size).pixmap(
            QSize(size, size)).toImage()
        red = QColor(active_palette().stop)
        for pixel in colors(window.stop, size):
            assert abs(pixel.red() - red.red()) <= 2
            assert abs(pixel.green() - red.green()) <= 2
            assert abs(pixel.blue() - red.blue()) <= 2
        # 停止是整块方片：没有挖空，也不和红棕色的“回到最新”共用图标。
        assert enclosed_holes(ink(window.stop, size), size) == 0
        assert window.stop.icon().cacheKey() != window.latest.icon().cacheKey()
        assert window.stop.icon().cacheKey() != share_icon("pause.png").cacheKey()
        app.processEvents()
    finally:
        window.close()
        journal.__exit__(None, None, None)
