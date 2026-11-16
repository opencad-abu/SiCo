"""左右面板条状收起：双击分割条切换、窄窗口自动收起、最小宽度保持完整。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_gui_wait import settle
from PyQt5.QtCore import QPoint, QSize, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.storage.journal import SessionJournal
from sico_ui import glyphs, workspace
from sico_ui.theme import ACCENT
from agent_window_fixture import create_window as AssistantWindow


def build_window(tmp_path, width=2000, height=820):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    journal = SessionJournal(tmp_path, "rail")
    journal.__enter__()
    loop = PythonBackend(
        ScriptedProvider([]), ToolRegistry(), journal, BoundContext("i", "g", "bound", {})
    )
    window = AssistantWindow(loop)
    window.show()
    # 先给足宽度再收窄：离屏屏幕很小，初始几何会顶到最小宽度并触发自动收条。
    window.resize(width, height)
    settle(window)
    app.processEvents()
    return app, journal, window


def separator_point(window, key):
    rect = window.panels[key].geometry()
    edge = rect.right() if key == "left" else rect.left()
    return QPoint(edge + (2 if key == "left" else -2), rect.center().y())


def rail_icon_image(window, key):
    size = workspace.RAIL_GLYPH_SIZE
    button = window.collapsible[key].rail.button
    assert not button.icon().isNull()
    return button.icon().pixmap(QSize(size, size)).toImage()


def column_heights(image, size):
    """图标每一列的可见像素数：三角形宽底那一列高，尖顶那一列矮。"""
    return [
        sum(1 for y in range(size) if image.pixelColor(x, y).alpha())
        for x in range(size)
    ]


def test_rails_paint_an_accent_triangle_instead_of_a_share_png(tmp_path):
    """条状手柄画 Qt 的红棕色小三角：左条向右、右条向左，不再用左右 png。"""
    app, journal, window = build_window(tmp_path)
    try:
        size = workspace.RAIL_GLYPH_SIZE
        for key, direction in (("left", Qt.RightArrow), ("right", Qt.LeftArrow)):
            assert window.set_panel_collapsed(key, True)
            app.processEvents()
            assert window.collapsible[key].rail.isVisible()
            image = rail_icon_image(window, key)
            assert image == glyphs.triangle_icon(direction, size).pixmap(
                QSize(size, size)).toImage()
            assert image.pixelColor(size // 2, size // 2) == QColor(ACCENT)
            heights = [height for height in column_heights(image, size) if height]
            assert len(heights) >= 3
            # 指向决定宽底在哪一侧：向右指的三角形底在左，向左指的底在右。
            assert (heights[0] > heights[-1]) == (direction == Qt.RightArrow)
    finally:
        window.close()
        journal.__exit__(None, None, None)


def test_separator_double_click_collapses_and_expands(tmp_path):
    app, journal, window = build_window(tmp_path)
    try:
        central = window.tabs.width()
        assert window.panels["left"].width() > workspace.RAIL_WIDTH * 2
        QTest.mouseDClick(window, Qt.LeftButton, Qt.NoModifier, separator_point(window, "left"))
        app.processEvents()
        assert window.panel_collapsed("left")
        assert window.panels["left"].width() <= workspace.RAIL_WIDTH + 4
        assert window.navigation.isHidden()
        assert window.collapsible["left"].rail.isVisible()
        assert window.tabs.width() > central
        # 同一个位置再次双击：条状手柄原位展开回面板。
        QTest.mouseDClick(window, Qt.LeftButton, Qt.NoModifier, separator_point(window, "left"))
        app.processEvents()
        assert not window.panel_collapsed("left")
        assert window.tabs.width() == central
        assert window.navigation.isVisible()
        assert not window.collapsible["left"].rail.isVisible()
    finally:
        window.close()
        journal.__exit__(None, None, None)


def test_rail_click_and_right_panel_follow_the_same_design(tmp_path):
    app, journal, window = build_window(tmp_path)
    try:
        central = window.tabs.width()
        assert window.set_panel_collapsed("right", True)
        app.processEvents()
        rail = window.collapsible["right"].rail
        assert window.panels["right"].width() <= workspace.RAIL_WIDTH + 4
        assert window.centers.isHidden() and rail.isVisible()
        assert window.tabs.width() > central
        rail.button.click()
        app.processEvents()
        assert not window.panel_collapsed("right")
        assert window.tabs.width() == central
        # 双击条状手柄本身也能展开。
        window.set_panel_collapsed("right", True)
        app.processEvents()
        QTest.mouseDClick(rail, Qt.LeftButton, Qt.NoModifier, rail.rect().center())
        app.processEvents()
        assert not window.panel_collapsed("right")
    finally:
        window.close()
        journal.__exit__(None, None, None)


def test_narrow_window_collapses_nav_and_wide_window_restores_it(tmp_path):
    app, journal, window = build_window(tmp_path)
    try:
        assert not window.panel_collapsed("left") and not window.panel_collapsed("right")
        # 窗口变窄：先收左侧导航；再窄下去连右侧中心一起收成条状。
        for width in (1100, 1000, 900, 800, 700, 640, 600):
            window.resize(width, 700)
            app.processEvents()
            # 收起顺序是左侧先于右侧；tab 内宽还扣除了 chrome/布局边距，
            # 不能拿它和基于窗口可用宽度的触发阈值做逐像素比较。
            assert not window.panel_collapsed("right") or window.panel_collapsed("left"), width
        assert window.panel_collapsed("left") and window.panel_collapsed("right")
        # 窗口变宽：先展回左侧导航，再展回右侧中心；中心聊天区始终保住可用宽度。
        window.resize(1300, 800)
        app.processEvents()
        assert not window.panel_collapsed("left")
        window.resize(1700, 800)
        app.processEvents()
        assert not window.panel_collapsed("right")
        assert window.tabs.width() >= workspace.CHAT_MIN_WIDTH
    finally:
        window.close()
        journal.__exit__(None, None, None)


def test_panels_keep_min_width_and_manual_expand_wins(tmp_path):
    app, journal, window = build_window(tmp_path)
    try:
        # 右侧中心面板和左侧导航一样有最小宽度：页签标题与筛选行不被压扁。
        tabs = window.centers.tabs
        assert window.centers.minimumWidth() >= sum(
            tabs.tabBar().tabSizeHint(i).width() for i in range(tabs.count())
        )
        for width in (1000, 900, 800, 700, 640, 600):
            window.resize(width, 700)
            app.processEvents()
        assert window.panel_collapsed("left")
        # 窄窗口里用户手动展开：不再被自动规则立刻收起。
        window.collapsible["left"].rail.button.click()
        app.processEvents()
        assert not window.panel_collapsed("left")
        window.resize(600, 700)
        app.processEvents()
        assert not window.panel_collapsed("left")
        # 窗口重新变宽：手动记录解锁，再窄下去仍会自动收条。
        window.resize(1700, 820)
        app.processEvents()
        assert not window.panel_collapsed("left") and not window.panel_collapsed("right")
        for width in (900, 800, 700, 640, 600):
            window.resize(width, 700)
            app.processEvents()
        assert window.panel_collapsed("left")
    finally:
        window.close()
        journal.__exit__(None, None, None)
