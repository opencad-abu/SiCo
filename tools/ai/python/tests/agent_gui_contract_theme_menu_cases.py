# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent gui contract theme menu contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import QPoint, QSize, Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QToolBar

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal
from sico_ui.theme import active_palette, window_stylesheet


def test_copilot_theme_uses_requested_palette(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "theme") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("theme-instance", "theme-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        stylesheet = window.styleSheet()
        palette = active_palette()
        accent = palette.accent
        assert palette.background in stylesheet and palette.text in stylesheet
        assert accent in stylesheet
        assert f"border-left: 1px solid {accent}" in stylesheet
        assert f"border-top: 1px solid {accent}" in stylesheet
        # Window menus and tab labels carry the accent colour.

        def rule(selector):
            return stylesheet.split(selector, 1)[1].split("}", 1)[0]

        assert f"color: {accent}" in rule("QMenuBar, QMenu,")
        assert f"color: {accent}" in rule("QTabBar::tab {")
        assert f"color: {accent}" in rule("QTabBar::tab:selected,")
        # Target, token usage, task/stage filters, status and model badge too.
        assert f"color: {accent}" in rule("QLabel#sessionTarget")
        # 全部下拉筛选（任务/阶段/执行任务、全部数据/全部来源）统一红棕色。
        assert f"color: {accent}" in rule("QComboBox, QComboBox QAbstractItemView")
        # 审阅页面的选项圆点：红棕描边的空心圆，选中时圆心补同色实心点。
        assert f"border: 2px solid {accent}" in rule("QRadioButton::indicator {")
        assert f"stop:0 {accent}" in rule("QRadioButton::indicator:checked {")
        for widget, name in (
            (window.target, "sessionTarget"),
            (window.token_usage, "tokenUsage"),
            (window.status, "sessionStatus"),
            (window.model_status, "modelStatus"),
            (window.centers.work_filter, "workFilter"),
            (window.centers.stage_filter, "stageFilter"),
            (window.centers.task_filter, "taskFilter"),
        ):
            assert widget.objectName() == name
        window.close()


def test_silicon_copilot_windows_share_logo_and_composer_theme(tmp_path):
    from pathlib import Path

    from sico_ui.branding import logo_icon, logo_path
    from sico_ui.composer import QuickComposer

    app = QApplication.instance() or QApplication([])
    logo = logo_path()
    assert logo is not None
    assert Path(logo).parts[-3:] == ("icons", "brand", "logo-dark.png")
    assert not logo_icon().isNull()
    with SessionJournal(tmp_path, "branding") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("branding-instance", "branding-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            assert window.windowIcon().cacheKey() == logo_icon().cacheKey()
            composer = QuickComposer(
                {"source": "测试库 / 电路", "destination": "Session: test"}, lambda line: None
            )
            try:
                assert composer.windowIcon().cacheKey() == logo_icon().cacheKey()
                assert window_stylesheet() in composer.styleSheet()
                assert "#copilotTitleBar" in composer.styleSheet()
            finally:
                composer.finished = True
                composer.close()
        finally:
            window.close()


def test_task_actions_stay_in_the_menu_and_input_area_without_a_toolbar(tmp_path):
    """The former top-left buttons duplicated the input area and the menu."""
    from agent_quick_fixture import QuickInput

    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "menu-actions") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("menu-instance", "menu-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            quick = QuickInput(window, object(), lambda kind, **data: None)
            assert window.findChildren(QToolBar) == []
            labels = [action.text() for action in window.session_menu.actions()]
            assert "停止当前任务" not in labels and "输入任务" not in labels
            assert "执行待办任务" in labels and "新会话" in labels
            # The input area keeps the running-task buttons and their wording.
            assert "停止当前任务" in window.stop.toolTip()
            assert window.resume.toolTip() == window.resume_action.toolTip()
            window.reset_layout()
            assert window.findChildren(QToolBar) == []
            app.processEvents()
        finally:
            window.close()


def test_close_entry_ends_the_session_menu_after_late_actions(tmp_path):
    """服务关联窗口是出厂形状：关闭行仍在最后，且不出现项目服务二级菜单。"""
    from agent_quick_fixture import QuickInput

    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "menu-tail") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("tail-instance", "tail-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            assert [action.text() for action in window.session_menu.actions()][-1] == "关闭SiCo"
            QuickInput(window, object(), lambda kind, **data: None)
            actions = list(window.session_menu.actions())
            assert actions[-1].text() == "关闭SiCo"
            assert actions[-2].isSeparator()
            labels = [action.text() for action in actions if not action.isSeparator()]
            assert labels[-1] == "关闭SiCo" and "新会话" in labels
            # 原“隐藏窗口”入口已删除；Ctrl+W 交给关闭行，不再留下重名入口。
            assert "隐藏窗口" not in labels
            # 进程内桌面没有项目服务连接：服务入口一个都不出现。
            assert not hasattr(window, "service_menu")
            assert "项目服务" not in labels and "核对与恢复当前会话…" not in labels
            # 关闭项是一行自绘控件：红色粗体，图标是同一支红色的 Qt 自绘叉。
            from PyQt5.QtGui import QColor
            from PyQt5.QtWidgets import QLabel, QStyle, QWidgetAction

            from sico_ui.glyphs import cross_icon
            from sico_ui.workspace import ClosingMenuEntry

            palette = active_palette()
            entry = actions[-1]
            assert isinstance(entry, QWidgetAction)
            row = entry.defaultWidget()
            assert isinstance(row, ClosingMenuEntry)
            assert row.objectName() == "closingEntry"
            mark = row.findChild(QLabel, "closingEntryIcon")
            caption = row.findChild(QLabel, "closingEntryText")
            assert mark is not None and caption is not None and caption.text() == "关闭SiCo"
            assert mark.pixmap() is not None
            expected = cross_icon(
                ClosingMenuEntry.ICON, palette.stop).pixmap(
                    ClosingMenuEntry.ICON, ClosingMenuEntry.ICON).toImage()
            assert mark.pixmap().toImage() == expected
            stylesheet = window.styleSheet()
            closing_rule = stylesheet.split("QLabel#closingEntryText {", 1)[1].split("}", 1)[0]
            assert f"color: {palette.error}" in closing_rule
            assert "font-weight: 700" in closing_rule

            # 与原生项同一套列：图标列 = frame + 2，文本列 = 图标列 + 22。
            window.session_menu.popup(QPoint(0, 0))
            app.processEvents()
            frame = window.session_menu.style().pixelMetric(QStyle.PM_DefaultFrameWidth)
            assert mark.geometry().x() == frame + ClosingMenuEntry.INSET
            icon_column = frame + ClosingMenuEntry.INSET + ClosingMenuEntry.ICON
            assert caption.geometry().x() == icon_column + ClosingMenuEntry.GAP
            assert caption.font().bold()
            row_image = row.grab().toImage()
            red = QColor(palette.error)
            assert any(row_image.pixelColor(x, y) == red
                       for y in range(row_image.height()) for x in range(row_image.width()))
            window.session_menu.close()
            fired = []
            row._callback = lambda: fired.append("closed")
            QTest.mouseClick(row, Qt.LeftButton)
            app.processEvents()
            assert fired == ["closed"]
            # 与原生项只差红字粗体：把行画在彩色画布上，不能有白底。
            from PyQt5.QtGui import QPixmap

            canvas = QPixmap(row.size())
            canvas.fill(QColor("#123456"))
            row.render(canvas)
            rendered = canvas.toImage()
            assert not any(
                (c := rendered.pixelColor(x, y)).alpha() and c.red() > 240
                and c.green() > 240 and c.blue() > 240
                for y in range(rendered.height()) for x in range(rendered.width())
            )
            # Ctrl+W 直接结束桌面：关闭行是唯一关闭入口，按键跟着它走。
            from PyQt5.QtWidgets import QWidget

            bound = next(action for action in QWidget.actions(window)
                         if action.text() == "关闭SiCo")
            assert bound.shortcut().toString() == "Ctrl+W"
            window.show()
            window.activateWindow()
            app.processEvents()
            QTest.keyClick(window, Qt.Key_W, Qt.ControlModifier)
            app.processEvents()
            assert window.lifecycle.closing
            app.processEvents()
        finally:
            window.close()


def test_service_window_keeps_only_closing_and_recovery_entries(tmp_path, monkeypatch):
    """服务窗口集中提供会话管理与恢复/核对；强制停止在“会话”右侧的“服务”菜单。"""
    from agent_service_gui_fixtures import remote_window
    from sico_ui.force_service_stop import FORCE_STOP
    from sico_ui.service_connection import attach_service_connection

    def label(action):
        return None if action.isSeparator() else action.text()

    app = QApplication.instance() or QApplication([])
    with remote_window(tmp_path, monkeypatch) as (_service, window):
        assert attach_service_connection(window) is window.service_connection
        window.session_action("新会话", lambda: None, "Ctrl+N")
        actions = list(window.session_menu.actions())
        assert [label(action) for action in actions] == [
            "返回当前会话", "会话历史与操作…", "执行待办任务",
            "核对与恢复当前会话…", "核对会话结束结果", "重试连接…", "新会话",
            None, "关闭SiCo",
        ]
        assert all(action.menu() is None for action in actions)
        # “服务”菜单紧跟“会话”，强制停止只在这里出现。
        assert [action.text() for action in window.menu_bar.actions()] == [
            "会话", "服务", "通信", "视图", "工作区", "设置", "工具"]
        assert [action.text() for action in window.service_menu.actions()] == ["停止", FORCE_STOP]
        assert window.service_connection.force_action.text() == "强制停止"
        # "核对会话结束结果"只在有未核对的原结束请求时出现。
        entries = {label(action): action for action in actions if not action.isSeparator()}
        assert not entries["核对会话结束结果"].isVisible()
        assert not entries["核对与恢复当前会话…"].isVisible()
        app.processEvents()


def test_waiting_audit_status_counts_queued_work_as_daiban():
    from sico_ui.tasks import session_status

    state = {
        "fault": "", "closing": False, "cancelling": False, "busy": True, "paused": False,
        "pending": 2, "task": {"status": "executing", "waiting_audits": True,
                               "activity": {"label": "等待答复：电源域选择"}},
    }
    assert session_status(state) == "等待答复；待办 2 条"
    state["pending"] = 0
    assert session_status(state) == "等待答复"


def test_only_the_composer_send_button_is_accent_filled(tmp_path):
    """强调色填充只标注主提交动作：这里是发送，审阅页面是“提交答复”。"""
    from sico_ui.theme import active_palette

    app = QApplication.instance() or QApplication([])
    palette = active_palette()

    def brightness(button, mode):
        image = button.icon().pixmap(QSize(18, 18), mode).toImage()
        samples = [
            image.pixelColor(x, y)
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 128
        ]
        assert samples
        return sum(c.red() + c.green() + c.blue() for c in samples) / (3 * len(samples))

    with SessionJournal(tmp_path, "bottom-actions") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("bottom-instance", "bottom-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            assert window.send.property("bottomAction") is True
            # The filled button carries a light glyph while it is usable …
            assert brightness(window.send, QIcon.Normal) > 200
            # … and keeps a readable text-colour glyph on the disabled surface.
            assert brightness(window.send, QIcon.Disabled) > 200
            for button in (window.stop, window.resume, window.abandon, window.latest):
                assert button.property("bottomAction") is not True
            stylesheet = window.styleSheet()
            assert f"background-color: {palette.accent}" in stylesheet.split(
                'QPushButton[bottomAction="true"],', 1)[1].split("}", 1)[0]
            disabled = stylesheet.split('QPushButton[bottomAction="true"]:disabled', 1)[1]
            assert f"background-color: {palette.background}" in disabled.split("}", 1)[0]
            app.processEvents()
        finally:
            window.close()


def test_action_buttons_paint_the_accent_glyphs_instead_of_share_icons(tmp_path):
    """会话、面板与列表上的按钮图标都是 Qt 字形，没有一个再引用 share 位图。"""
    from agent_quick_fixture import QuickInput
    from sico_ui.branding import icon, share_path
    from sico_ui.glyph_plates import plate_arrow_icon, plate_check_icon, plate_text_icon, stop_icon
    from sico_ui.glyphs import (
        ACTION_GLYPH_SIZE,
        back_icon,
        cross_icon,
        info_icon,
        plus_icon,
        refresh_icon,
        send_icon,
        triangle_icon,
    )
    from sico_ui.theme import active_palette

    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "icons") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("icons-instance", "icons-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop, persistent=True)
        try:
            quick = QuickInput(window, object(), lambda kind: None)
            size = ACTION_GLYPH_SIZE
            for widget, name, glyph in (
                (window.stop, "pause.png", stop_icon(size)),
                (window.resume_action, "start.png", triangle_icon(Qt.RightArrow, size)),
                (window.return_action, "back.png", back_icon(size)),
                (quick.new, "new1.png", plus_icon(size)),
                (window.centers.reports.previous, "left.png", plate_arrow_icon(Qt.LeftArrow)),
                (window.centers.reports.next, "right.png", plate_arrow_icon(Qt.RightArrow)),
                (window.centers.reports.open_button, "text.png", plate_text_icon()),
                (window.detail.back, "back.png", back_icon(size)),
                (window.detail.refresh, "refresh.png", refresh_icon(size)),
                (window.binding_panel.select, "check.png", plate_check_icon()),
                (window.binding_panel.release, "uncheck.png", cross_icon(size)),
            ) + tuple(
                # 路由回执/重连两个入口随路由功能一起进来，尚未提交时跳过。
                (action, name, glyph)
                for action, name, glyph in (
                    (getattr(window, "receipt_action", None), "info2.png", info_icon(size)),
                    (getattr(window, "reconnect_action", None), "connect.png", refresh_icon(size)),
                )
                if action is not None
            ):
                assert not widget.icon().isNull()
                assert share_path(name), name
                painted = widget.icon().pixmap(QSize(size, size)).toImage()
                assert painted == glyph.pixmap(QSize(size, size)).toImage(), name
                assert widget.icon().cacheKey() != icon(name).cacheKey(), name
            # 发送按钮的纸飞机先按正文色画：可用时由 filled_icon 提亮，禁用时保持深色。
            send = window.send.icon().pixmap(QSize(size, size), QIcon.Disabled).toImage()
            expected = send_icon(
                size, active_palette().text).pixmap(QSize(size, size)).toImage()
            assert send == expected
            assert window.send.icon().cacheKey() != icon("send.png").cacheKey()
        finally:
            window.close()
