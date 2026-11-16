# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent gui contract chrome contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import QPoint, QSize, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal
from sico_ui.glyphs import (
    ACTION_GLYPH_SIZE,
    TITLE_GLYPH_SIZE,
    close_icon,
    maximize_icon,
    minimize_icon,
    terminal_icon,
)
from sico_ui.theme import active_palette


def test_frameless_window_chrome_drives_the_window(tmp_path):
    from sico import PRODUCT_NAME
    from sico_ui.branding import icon, share_path

    app = QApplication.instance() or QApplication([])
    palette = active_palette()
    with SessionJournal(tmp_path, "chrome") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("chrome-instance", "chrome-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            window.show()
            app.processEvents()
            assert window.windowFlags() & Qt.FramelessWindowHint
            assert window.title_bar.title.text() == PRODUCT_NAME
            assert [action.text() for action in window.menu_bar.actions()] == [
                "会话",
                "视图",
                "工作区",
                "设置",
                "工具",
            ]
            view_menu = next(action.menu() for action in window.menu_bar.actions()
                             if action.text() == "视图")
            assert [action.text() for action in view_menu.actions()] == [
                "恢复默认布局", "导航", "中心"]
            workspace_menu = next(action.menu() for action in window.menu_bar.actions()
                                  if action.text() == "工作区")
            assert [action.text() for action in workspace_menu.actions()] == [
                "打开预览产物…"]
            tools_menu = next(action.menu() for action in window.menu_bar.actions()
                              if action.text() == "工具")
            assert [action.text() for action in tools_menu.actions()] == ["终端"]
            terminal = tools_menu.actions()[0].icon()
            assert terminal.pixmap(QSize(ACTION_GLYPH_SIZE, ACTION_GLYPH_SIZE)).toImage() == \
                terminal_icon(ACTION_GLYPH_SIZE).pixmap(
                    QSize(ACTION_GLYPH_SIZE, ACTION_GLYPH_SIZE)).toImage()
            # 标题栏三个按钮画 Qt 字形：最小化/最大化红棕，关闭保留红色。
            for key, glyph, color in (
                ("minimize", minimize_icon, QColor(palette.accent)),
                ("maximize", maximize_icon, QColor(palette.accent)),
                ("close", close_icon, QColor(palette.stop)),
            ):
                button = window.title_bar.buttons[key]
                assert not button.icon().isNull()
                image = button.icon().pixmap(QSize(TITLE_GLYPH_SIZE, TITLE_GLYPH_SIZE)).toImage()
                assert image == glyph(TITLE_GLYPH_SIZE).pixmap(
                    QSize(TITLE_GLYPH_SIZE, TITLE_GLYPH_SIZE)).toImage()
                visible = [image.pixelColor(x, y) for y in range(image.height())
                           for x in range(image.width()) if image.pixelColor(x, y).alpha() > 128]
                assert visible
                for pixel in visible:
                    assert abs(pixel.red() - color.red()) <= 2, key
                    assert abs(pixel.green() - color.green()) <= 2, key
                    assert abs(pixel.blue() - color.blue()) <= 2, key
            window.title_bar.buttons["maximize"].click()
            app.processEvents()
            assert window.isMaximized()
            window.title_bar.buttons["maximize"].click()
            app.processEvents()
            assert not window.isMaximized()
            window.title_bar.buttons["minimize"].click()
            app.processEvents()
            assert window.isMinimized()
            window.showNormal()
            app.processEvents()
            window.title_bar.buttons["close"].click()
            app.processEvents()
            assert not window.isVisible()
        finally:
            window.close()


def test_startup_progress_wears_the_frameless_chrome_and_a_red_bar():
    """启动小窗与其它窗口同一套无头外壳，进度条是红棕色。"""
    import pytest

    from sico import PRODUCT_NAME
    from sico_ui.chrome import SiDialog
    from sico_ui.theme import active_palette

    startup = pytest.importorskip("sico_ui.startup")  # 启动视图随该模块一起进入

    app = QApplication.instance() or QApplication([])
    dialog = startup.StartupDialog()
    try:
        assert isinstance(dialog, SiDialog)
        assert dialog.windowFlags() & Qt.FramelessWindowHint
        assert dialog.title_bar.title.text() == PRODUCT_NAME
        assert dialog.status.text() == "正在等待工程连接…"
        dialog.setLabelText("正在连接工程…")
        assert dialog.status.text() == "正在连接工程…"
        assert (dialog.bar.minimum(), dialog.bar.maximum()) == (0, 0)
        assert not dialog.bar.isTextVisible()
        chunk = dialog.styleSheet().split("QProgressBar::chunk {", 1)[1].split("}", 1)[0]
        assert f"background-color: {active_palette().accent}" in chunk
        fired = []
        dialog.canceled.connect(lambda: fired.append("canceled"))
        dialog.cancel.click()
        app.processEvents()
        assert fired == ["canceled"]
    finally:
        dialog.close()


def test_sico_windows_open_on_their_own_screen_half(tmp_path):
    """主窗口占左半屏；对话框贴在 SiCo 中心（Virtuoso 窗口的右半屏在 SKILL 侧）。"""
    from PyQt5.QtWidgets import QApplication as _App

    from sico_ui.chrome import SiDialog, half_screen_rect
    from sico_ui.composer import QuickComposer

    app = _App.instance() or _App([])
    left = half_screen_rect("left")
    assert left is not None

    with SessionJournal(tmp_path, "placement") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("place-instance", "place-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            assert window.default_geometry() == left
            window.show()
            app.processEvents()

            dialog = SiDialog("测试对话框", window)
            dialog.resize(320, 200)
            dialog.show()
            app.processEvents()
            try:
                centre, placed = window.frameGeometry().center(), dialog.frameGeometry().center()
                assert abs(placed.x() - centre.x()) <= 1
                assert abs(placed.y() - centre.y()) <= 1
            finally:
                dialog.deleteLater()

            composer = QuickComposer(
                {"source": "测试库 / 电路", "destination": "Session: test"}, lambda text: None
            )
            composer.show()
            app.processEvents()
            try:
                # 独立进程里的输入框没有锚窗口：贴 SiCo 所在的左半屏中心，尺寸不变。
                placed = composer.frameGeometry().center()
                assert abs(placed.x() - left.center().x()) <= 1
                assert abs(placed.y() - left.center().y()) <= 1
                assert composer.size().height() < left.height()
            finally:
                composer.close()
                composer.deleteLater()
            app.processEvents()
        finally:
            window.close()


def test_title_drag_hands_the_move_to_the_window_manager(tmp_path):
    """贴边半屏是 WM 的能力：自绘标题栏必须把搬运交给它，而不是自己 move()。"""
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "drag-system") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("drag-instance", "drag-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            window.show()
            app.processEvents()

            class Handle:
                def __init__(self, accepts):
                    self.accepts, self.calls = accepts, 0

                def startSystemMove(self):
                    self.calls += 1
                    return self.accepts

            accepted = Handle(True)
            window.system_move_handle = lambda: accepted
            window.begin_drag(QPoint(120, 90))
            assert accepted.calls == 1
            # WM 接管后不再自己搬窗口，避免与贴边/半屏的效果打架。
            assert window._frameless_drag is None
            position = window.pos()
            window.continue_drag(QPoint(300, 260))
            assert window.pos() == position

            refused = Handle(False)
            window.system_move_handle = lambda: refused
            window.begin_drag(QPoint(window.x() + 20, window.y() + 20))
            assert refused.calls == 1 and window._frameless_drag is not None
            window.continue_drag(QPoint(window.x() + 60, window.y() + 50))
            assert window.pos() != position  # 平台不支持时仍能拖动
            window.end_drag_resize()
        finally:
            window.close()


def test_frameless_composer_reuses_the_window_chrome():
    from sico_ui.composer import QuickComposer

    app = QApplication.instance() or QApplication([])
    composer = QuickComposer({"source": "测试库 / 电路", "destination": "Session: test"},
                             lambda line: None)
    try:
        assert composer.windowFlags() & Qt.FramelessWindowHint
        assert composer.windowTitle() == "Ask Silicon Copilot"
        assert composer.title_bar.title.text() == "Ask Silicon Copilot"
        assert sorted(composer.title_bar.buttons) == ["close", "maximize", "minimize"]
        assert composer.title_bar.height() == 32
        composer.show()
        app.processEvents()
        composer.title_bar.buttons["close"].click()
        assert composer.finished and not composer.isVisible()
    finally:
        composer.finished = True
        composer.close()


def test_window_component_chromes_new_windows_automatically():
    from sico_ui.chrome import SiDialog, SiMainWindow, SiWidget
    from sico_ui.theme import window_stylesheet

    app = QApplication.instance() or QApplication([])

    class ProbeWindow(SiMainWindow):
        pass

    class ProbeDialog(SiDialog):
        pass

    window = ProbeWindow("组件验证窗口")
    dialog = ProbeDialog("组件验证对话框")
    surface_widget = SiWidget("组件验证控件窗")
    trimmed = ProbeDialog("只有关闭键", controls=("close",))
    try:
        for surface in (window, dialog, surface_widget, trimmed):
            assert surface.windowFlags() & Qt.FramelessWindowHint
            assert surface.title_bar.height() == 32
            assert surface.title_bar.rule.height() == 3
            assert surface.styleSheet() == window_stylesheet()
        assert window.windowTitle() == "组件验证窗口"
        assert sorted(window.title_bar.buttons) == ["close", "maximize", "minimize"]
        assert sorted(dialog.title_bar.buttons) == ["close", "maximize", "minimize"]
        assert sorted(trimmed.title_bar.buttons) == ["close"]
        assert dialog.content_layout() is dialog.body_layout
    finally:
        for surface in (window, dialog, surface_widget, trimmed):
            surface.close()


def test_frameless_drag_and_edge_resize_move_the_window(tmp_path):
    from PyQt5.QtCore import QEvent, QPoint
    from PyQt5.QtGui import QMouseEvent

    def mouse(kind, local):
        global_position = window.pos() + local
        return QMouseEvent(
            kind, QPoint(local), QPoint(global_position),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
        )

    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "drag") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("drag-instance", "drag-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            window.move(120, 120)
            window.show()
            app.processEvents()
            start = window.pos()
            QApplication.sendEvent(window, mouse(QEvent.MouseButtonPress, QPoint(400, 16)))
            QApplication.sendEvent(window, mouse(QEvent.MouseMove, QPoint(460, 56)))
            QApplication.sendEvent(window, mouse(QEvent.MouseButtonRelease, QPoint(460, 56)))
            app.processEvents()
            assert window.pos() == start + QPoint(60, 40)
            width = window.width()
            edge = QPoint(width - 2, window.height() // 2)
            QApplication.sendEvent(window, mouse(QEvent.MouseButtonPress, edge))
            QApplication.sendEvent(
                window, mouse(QEvent.MouseMove, QPoint(edge.x() + 40, edge.y()))
            )
            QApplication.sendEvent(
                window, mouse(QEvent.MouseButtonRelease, QPoint(edge.x() + 40, edge.y()))
            )
            app.processEvents()
            assert window.width() == width + 40
        finally:
            window.close()


def test_edge_grips_and_title_bar_move_the_window(tmp_path):
    from PyQt5.QtCore import QEvent, QPoint
    from PyQt5.QtGui import QMouseEvent

    def event(kind, widget, local):
        global_position = widget.mapToGlobal(QPoint(local)) if widget.isVisible() else QPoint(local)
        return QMouseEvent(
            kind, QPoint(local), QPoint(global_position),
            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
        )

    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "grips") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("grips-instance", "grips-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            window.move(150, 150)
            window.resize(900, 620)
            window.show()
            app.processEvents()
            grips = {grip.direction: grip for grip in window._frameless_grips}
            assert sorted(grips) == [
                "bottom", "bottomLeft", "bottomRight", "left",
                "right", "top", "topLeft", "topRight",
            ]
            start = window.pos()
            bar = window.title_bar
            QApplication.sendEvent(bar, event(QEvent.MouseButtonPress, bar, QPoint(300, 16)))
            QApplication.sendEvent(bar, event(QEvent.MouseMove, bar, QPoint(360, 66)))
            QApplication.sendEvent(bar, event(QEvent.MouseButtonRelease, bar, QPoint(360, 66)))
            app.processEvents()
            assert window.pos() == start + QPoint(60, 50)
            width = window.width()
            grip = grips["right"]
            QApplication.sendEvent(grip, event(QEvent.MouseButtonPress, grip, QPoint(3, 200)))
            QApplication.sendEvent(grip, event(QEvent.MouseMove, grip, QPoint(63, 200)))
            QApplication.sendEvent(grip, event(QEvent.MouseButtonRelease, grip, QPoint(63, 200)))
            app.processEvents()
            assert window.width() == width + 60
        finally:
            window.close()
