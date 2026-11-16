# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent gui contract workspace layout contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_gui_wait import activate, settle
from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal


def test_token_usage_uses_compact_units(tmp_path):
    from sico_ui.presentation import token_amount

    assert token_amount(0) == "0"
    assert token_amount(999) == "999"
    assert token_amount(1000) == "1.0K"
    assert token_amount(1234) == "1.2K"
    assert token_amount(15_500) == "15.5K"
    assert token_amount(999_499) == "999.5K"
    # 一位小数会进位到 1000.0 时改用更大单位。
    assert token_amount(999_999) == "1.0M"
    assert token_amount(12_345_678) == "12.3M"
    assert token_amount(3_456_789_012) == "3.5B"
    assert token_amount(-5) == "0"

    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "usage") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("usage-instance", "usage-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            window.renderer._update_token_usage({"input_tokens": 1234, "output_tokens": 2_000_000})
            assert window.token_usage.fullText() == "词元：输入 1.2K · 输出 2.0M · 合计 2.0M"
            window.renderer._update_token_usage({})
            assert window.token_usage.fullText() == "词元：输入 0 · 输出 0 · 合计 0"
        finally:
            window.close()


def test_record_lists_show_time_in_its_own_column():
    from sico_ui.presentation import event_time
    from sico_ui.records import AuditPanel
    from sico_ui.workbench_view import ObjectList

    app = QApplication.instance() or QApplication([])
    stamp = "2026-09-14T02:31:00+00:00"
    expected = event_time(stamp)
    assert expected
    event = {"timestamp": stamp, "session_id": "session", "task_id": "task"}

    panel = AuditPanel("暂无交互记录")
    panel.add("你 · 读一下当前窗口", event, markdown="读一下当前窗口")
    item = panel.list.topLevelItem(0)
    assert item.text(0) == expected
    assert item.text(1) == "你 · 读一下当前窗口"
    row = {
        "id": "audit-1", "title": "规格确认", "timestamp": stamp,
        "questions": [{"header": "运行方式", "question": "前台还是后台？"}],
    }
    panel.add_audit(row, event)
    audit_item = panel.list.topLevelItem(1)
    assert audit_item.text(0) == expected
    assert audit_item.text(1) == "审阅事项 · 规格确认"
    panel.add("无时间记录", {"session_id": "session"}, markdown="内容")
    assert panel.list.topLevelItem(2).text(0) == ""
    assert panel.list.topLevelItem(2).text(1) == "无时间记录"

    listing = ObjectList("report")

    page = {"offset": 0, "total": 1, "version": 1, "page_size": 200,
            "selected_id": "", "locate_id": "", "target_found": False, "sources": (),
            "rows": [{"key": "report-1", "label": "执行记录 · LDO 验证",
                      "stamp": expected, "state_label": "范围已完成", "stage_title": "阶段"}]}
    listing.apply_page(page)
    report_item = listing.list.topLevelItem(0)
    assert report_item.text(0) == expected
    assert report_item.text(1).startswith("执行记录 · ")
    page["rows"][0]["stamp"] = ""
    listing.apply_page(page)
    assert listing.list.topLevelItem(0).text(0) == ""
    assert listing.list.topLevelItem(0).text(1).startswith("执行记录 · ")


def test_token_usage_stays_on_one_line_and_elides(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "usage-line") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("line-instance", "line-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            window.show()
            app.processEvents()
            label = window.token_usage
            assert not label.wordWrap()
            window.renderer._update_token_usage({"input_tokens": 999_999_999_999,
                                        "output_tokens": 999_999_999_999})
            app.processEvents()
            assert "\n" not in label.text()
            assert label.fullText() == "词元：输入 1000.0B · 输出 1000.0B · 合计 2000.0B"
            # squeezed: keep one line, elide the middle, retain the full value
            label.setFixedWidth(120)
            app.processEvents()
            assert "\n" not in label.text() and "…" in label.text()
            assert label.fullText().startswith("词元：输入 ") and label.fullText().endswith("合计 2000.0B")
            assert "2000.0B" in label.toolTip()
        finally:
            window.close()


def test_corner_badge_shows_base_and_model(tmp_path):
    from sico.codex.settings import codex_base_label

    # 角标直接用完整版本号，不再截断成 codex-v0.156。
    assert codex_base_label("0.162.1") == "codex-0.162.1"
    assert codex_base_label("1.2.3") == "codex-1.2.3"
    assert codex_base_label("2") == "codex-2"

    class CodexLike(ContextDemoProvider):
        base = "codex-0.162.1"
        model = "deepseek-v4-flash"
        label = "Codex 0.162.1 / deepseek-v4-flash / native"

    from sico import PRODUCT_NAME

    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "badge") as journal:
        loop = PythonBackend(
            CodexLike(), ToolRegistry(), journal,
            BoundContext("badge-instance", "badge-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            assert window.model_status.text() == "基座：codex-0.162.1\n模型：deepseek-v4-flash"
            assert window.windowTitle() == PRODUCT_NAME
        finally:
            window.close()
    with SessionJournal(tmp_path, "badge-fallback") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("fallback-instance", "fallback-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            assert window.model_status.text() == "模拟模型（无模型请求）"
            assert "\n" not in window.model_status.text()
        finally:
            window.close()


def test_workspace_status_bar_stays_on_one_line(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "status-bar") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("status-instance", "status-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            assert not window.status.wordWrap()
            assert not window.model_status.wordWrap()
            assert window.status.height() <= window.fontMetrics().height() + 4
            assert window.model_status.height() <= window.fontMetrics().height() + 4
            assert window.statusBar().height() <= window.fontMetrics().height() + 12
        finally:
            window.close()


def test_token_usage_is_shown_beside_current_target(tmp_path):
    from dataclasses import replace

    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "token-usage") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("token-instance", "token-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            state = window.controller.updates.read_updates()["snapshot"]
            state["task"].update(input_tokens=1234, output_tokens=56)
            window.renderer.update_session(state)
            assert window.token_usage.fullText() == "词元：输入 1.2K · 输出 56 · 合计 1.3K"
            assert window.token_usage.toolTip()
            # 顶部状态信息用小字号；底部状态栏固定为 6pt，不随标签字号放大。
            window.page.view = replace(window.page.view, base="codex-0.162.1",
                                          model="deepseek-v4-flash")
            window.refresh_model_badge()
            assert window.readout_point_size() == min(
                window.base_point_size(), max(9, window.base_point_size() - 2))
            assert window.status_point_size() == 6
            assert window.target.font().pointSize() == window.readout_point_size()
            assert window.token_usage.font().pointSize() == window.readout_point_size()
            assert window.model_status.font().pointSize() == window.status_point_size()
            assert window.status_point_size() <= window.readout_point_size()
        finally:
            window.close()


def test_busy_cursor_follows_page_loading_not_the_running_model(tmp_path):
    app = QApplication.instance() or QApplication([])
    while QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()
    with SessionJournal(tmp_path, "busy-cursor") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("busy-instance", "busy-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        window.show()
        settle(window)
        try:
            viewport = window.display.viewport()
            # 模型思考/工具执行不占用光标：气泡里的工具结果始终看起来可点。
            window.page.opening = False
            window.renderer.refresh_busy_cursor()
            state = window.controller.updates.read_updates()["snapshot"]
            state["busy"] = True
            window.renderer.update_session(state)
            QTest.mouseMove(viewport, viewport.rect().center())
            app.processEvents()
            assert viewport.cursor().shape() != Qt.BusyCursor
            assert QApplication.overrideCursor() is None

            # 读取历史/打开会话这类把页面读进来的过程才在对话区显示 busy。
            window.page.opening = True
            window.renderer.refresh_busy_cursor()
            QTest.mouseMove(viewport, viewport.rect().center())
            app.processEvents()
            assert viewport.cursor().shape() == Qt.BusyCursor
            # 窗口与输入框始终不用 busy，也不再劫持全局光标。
            assert window.cursor().shape() != Qt.BusyCursor
            assert window.input.viewport().cursor().shape() != Qt.BusyCursor
            assert window.navigation.cursor().shape() != Qt.BusyCursor
            assert window.centers.cursor().shape() != Qt.BusyCursor

            window.page.opening = False
            window.renderer.refresh_busy_cursor()
            # 执行结束后不需要再移动鼠标也会恢复。
            assert viewport.cursor().shape() == Qt.ArrowCursor
            assert QApplication.overrideCursor() is None
        finally:
            window.close()
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()


def test_data_loads_own_the_busy_cursor_in_their_widget():
    """加载数据只在对应控件里显示 busy：列表查询与详情读取。"""
    app = QApplication.instance() or QApplication([])
    from sico_ui.workbench_list import ObjectList
    from sico_ui.workbench_view import WorkbenchDetail

    listing = ObjectList("report")
    listing.show()
    app.processEvents()
    listing.set_pending(True)
    QTest.mouseMove(listing.list.viewport(), listing.list.viewport().rect().center())
    app.processEvents()
    assert listing.list.viewport().cursor().shape() == Qt.BusyCursor
    listing.set_pending(False)
    assert listing.list.viewport().cursor().shape() != Qt.BusyCursor
    listing.close()

    detail = WorkbenchDetail()
    detail.show()
    app.processEvents()
    detail.document.set_busy(True)
    QTest.mouseMove(detail.document.viewport(), detail.document.viewport().rect().center())
    app.processEvents()
    assert detail.document.viewport().cursor().shape() == Qt.BusyCursor
    detail.document.set_busy(False)
    assert detail.document.viewport().cursor().shape() != Qt.BusyCursor
    detail.close()


def test_return_to_latest_scrolls_current_session_without_switching(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "latest-message") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("latest-instance", "latest-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        settle(window)
        window.presentation.transcript.append(
            "assistant", "\n\n".join(f"历史消息 {i}" for i in range(180))
        )
        window.show()
        window.renderer.flush()
        app.processEvents()
        try:
            bar = window.display.verticalScrollBar()
            assert bar.maximum() > 0
            bar.setValue(0)
            switched = []
            window.navigation.return_live = lambda: switched.append(True)

            window.latest.click()

            assert switched == []
            assert window.controller.session_id == journal.session_id
            assert window.tabs.currentWidget() is window.pages["conversation"]
            assert bar.value() == bar.maximum()
            assert not window.latest.isEnabled()
            assert window.latest.toolTip() == "滚动到会话底部"
        finally:
            window.close()


def test_return_to_latest_keeps_a_history_session_selected(tmp_path):
    app = QApplication.instance() or QApplication([])
    context = BoundContext("history-instance", "history-generation", "bound", {"valid": True})
    with SessionJournal(tmp_path, "history-selected") as history:
        history_loop = PythonBackend(ContextDemoProvider(), ToolRegistry(), history, context)
        history_loop._event("session.created", {})
    with SessionJournal(tmp_path, "latest-history") as journal:
        loop = PythonBackend(ContextDemoProvider(), ToolRegistry(), journal, context)
        window = AssistantWindow(loop)
        activate(window, "history-selected")
        window.presentation.transcript.append(
            "assistant", "\n\n".join(f"历史会话消息 {i}" for i in range(180))
        )
        window.show()
        window.renderer.flush()
        app.processEvents()
        try:
            bar = window.display.verticalScrollBar()
            assert bar.maximum() > 0
            bar.setValue(0)
            # 激活后的会话行在"当前"区域，选中状态不随滚动变化。
            item = window.navigation.current.currentItem()
            assert item is not None
            assert item.data(0, Qt.UserRole) == "history-selected"
            switched = []
            window.navigation.return_live = lambda: switched.append(True)

            window.latest.click()

            assert switched == []
            assert window.controller.session_id == "history-selected"
            assert window.navigation.current.currentItem().data(
                0, Qt.UserRole) == "history-selected"
            assert bar.value() == bar.maximum()
        finally:
            window.close()


def test_workspace_and_center_tabs_divide_available_width_evenly(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "equal-tabs") as journal:
        window = AssistantWindow(
            PythonBackend(ContextDemoProvider(), ToolRegistry(), journal,
                      BoundContext("i", "g", "bound", {"valid": True}))
        )
        window.resize(1000, 700)
        window.show()
        try:
            app.processEvents()
            for tabs in (window.tabs, window.centers.tabs):
                widths = [tabs.tabBar().tabRect(i).width() for i in range(tabs.count())]
                assert max(widths) - min(widths) <= 1
        finally:
            window.close()
