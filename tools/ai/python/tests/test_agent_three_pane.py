"""Role alignment, automatic context evidence and history/live isolation in Qt."""

from __future__ import annotations

import os
from functools import partial

import pytest
from agent_gui_wait import settle

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QShortcut

from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext, ModelTurn, TextDelta
from sico.service.python_backend import PythonBackend
from sico.core.startup import INITIAL_PROMPT
from sico.providers.mock import ScriptedProvider
from sico.service.workbench_data import WorkbenchService
from sico.storage.journal import SessionJournal
from sico_ui.tool_results import ToolResults
from agent_window_fixture import create_window as AssistantWindow


@pytest.mark.parametrize("width,height", [(800, 600), (1280, 820)])
def test_shared_startup_context_is_readable_without_raw_protocol(tmp_path, width, height):
    app = QApplication.instance() or QApplication([])
    bound = BoundContext("i", "g", "source", {
        "cellview": {"lib": "AnalogWork", "cell": "amplifier_tb", "view_name": "maestro"},
        "capabilities": ["get_entry_context"],
    })

    class Broker:
        def read(self, context, method, args):
            return {
                "ok": True, "valid": True, "schema": "cad.entry.context.v1",
                "session_ref": "fixture",
                "source": {
                    "window_ref": "wctx_fixture_1", "kind": "ade",
                    "cellview": bound.snapshot["cellview"],
                },
                "ade": {
                    "selected_test": "AC_nominal", "selected_history": "Interactive.2",
                    "test_count": 2, "results_evaluated": False,
                },
                "internal_details": {"private_runtime": "internal-value"},
            }

    with SessionJournal(tmp_path, "summary") as journal:
        provider = ScriptedProvider([])
        window = AssistantWindow(PythonBackend(provider, context_tools(Broker()), journal, bound))
        window.resize(width, height)
        window.show()
        try:
            assert window.controller.initialize_context()
            assert window.controller.wait(5)
            settle(window)
            app.processEvents()
            shown = window.display.toPlainText()
            assert all(value in shown for value in (
                "AnalogWork", "amplifier_tb", "AC_nominal", "Interactive.2", "当前 ADE 设置",
            ))
            assert not any(value in shown for value in (
                "cad.entry.context.v1", "wctx_fixture_1", "internal-value", "get_context", "{",
            ))
            assert not provider.requests
            result = next(e for e in journal.events() if e["kind"] == "tool.finished")
            assert result["payload"]["result"]["data"]["internal_details"] == {
                "private_runtime": "internal-value",
            }
            window.grab().save(str(tmp_path / "readable-context.png"))
        finally:
            window.close()


def test_new_context_role_alignment_centers_and_history_preserve_live_source(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    old = BoundContext(
        "old-i", "old-g", "old",
        {"cellview": {"lib": "past", "cell": "amp", "view_name": "schematic"}}
    )
    current = BoundContext(
        "i",
        "g",
        "bound",
        {"valid": True, "cellview": {"lib": "live", "cell": "top", "view_name": "schematic"}},
    )

    class Broker:
        def get_context(self, context):
            return context.snapshot

    with SessionJournal(tmp_path, "old") as journal:
        provider = ScriptedProvider([[TextDelta("历史回复"), ModelTurn("历史回复")]])
        list(PythonBackend(provider, context_tools(Broker()), journal, old).run("历史请求"))
    with SessionJournal(tmp_path, "live") as journal:
        reply = (
            "左侧回复\n\n| 项目 | 结果 |\n| --- | --- |\n| 绑定 | 有效 |\n\n"
            "```skill\ngeGetEditCellView()\n```"
        )
        provider = ScriptedProvider(
            [[TextDelta(reply), ModelTurn(reply)], [TextDelta("后台完成"), ModelTurn("后台完成")]]
        )
        window = AssistantWindow(PythonBackend(provider, context_tools(Broker()), journal, current))
        window.show()
        try:
            assert window.controller.initialize_context()
            assert window.controller.wait(5)
            settle(window)
            assert not provider.requests
            text = window.display.toPlainText()
            assert "当前设计上下文" in text and "live" in text and "top" in text
            assert "get_context" not in text and '"cellview"' not in text
            assert INITIAL_PROMPT not in text
            assert window.dockWidgetArea(window.panels["left"]) == Qt.LeftDockWidgetArea
            assert window.dockWidgetArea(window.panels["right"]) == Qt.RightDockWidgetArea
            assert [window.centers.tabText(i) for i in range(3)] == [
                "审阅",
                "汇报",
                "数据",
            ]
            assert [window.centers.data.category.itemText(i) for i in range(4)] == [
                "全部数据", "来源", "过程", "产出",
            ]
            app.processEvents()
            window.grab().save(str(tmp_path / "startup-context.png"))
            # The same phrase typed explicitly by a person remains visible.
            window.input.setPlainText(INITIAL_PROMPT + "\n右侧第二行 <b>保持原文</b>")
            window.actions.submit()
            settle(window)
            assert window.controller.wait(5)
            window.binding.poll()
            doc = window.display.document()
            for text in (INITIAL_PROMPT, "右侧第二行"):
                cursor = doc.find(text)
                assert cursor.blockFormat().alignment() == Qt.AlignLeft
                assert cursor.currentTable() is not None
                assert cursor.currentTable().format().alignment() == Qt.AlignRight
            copilot = doc.find("左侧回复")
            assert copilot.blockFormat().alignment() == Qt.AlignLeft
            assert copilot.currentTable() is not None
            assert copilot.currentTable().format().alignment() == Qt.AlignLeft
            assert "<b>保持原文</b>" in doc.toPlainText()
            assert "geGetEditCellView()" in doc.toPlainText()
            assert len(doc.rootFrame().childFrames()) >= 1
            assert window.centers.reports.list.topLevelItemCount() == 1
            assert window.centers.data.list.topLevelItemCount() == 4
            # 审阅栏只列询问/确认/选择事项；这条会话没有审阅，因此为空。
            assert window.centers.audit.list.topLevelItemCount() == 0
            assert window.centers.index.report_list()[0]["kind"] == "execution"
            app.processEvents()
            window.grab().save(str(tmp_path / "three-pane-conversation.png"))
            window.input.setPlainText("当前会话草稿")
            window.navigation.refresh()
            old_item = next(
                window.navigation.list.topLevelItem(i)
                for i in range(2)
                if window.navigation.list.topLevelItem(i).data(0, Qt.UserRole) == "old"
            )
            # 双击（itemDoubleClicked）进入只读回放；单击选中不改变中央视图。
            window.navigation.list.itemDoubleClicked.emit(old_item, 0)
            settle(window, tasks=False)
            # 只回放历史记录：现场会话、绑定目标和草稿都不变。
            assert window.page.reviewing == "old"
            assert not window.input.isEnabled() and not window.send.isEnabled()
            assert "历史回复" in window.display.toPlainText()
            assert window.target.text().startswith("历史会话（只读）")
            assert window.controller.session_id == "live"
            assert window.presentation.displayed_context == current
            window.binding.poll()
            assert window.presentation.displayed_context == current
            assert window.centers.index.session_id == "old"
            assert "历史回复" in window.display.toPlainText()
            app.processEvents()
            window.grab().save(str(tmp_path / "history-session.png"))
            # 返回现场后正文、中心记录和草稿恢复。
            shortcut = next(item for item in window.findChildren(QShortcut)
                            if item.key().toString() == "Ctrl+L")
            shortcut.activated.emit()
            settle(window, tasks=False)
            assert window.page.reviewing is None
            assert window.controller.session_id == "live"
            assert window.send.isEnabled() and window.input.isEnabled()
            assert window.input.toPlainText() == "当前会话草稿"
            assert "左侧回复" in window.display.toPlainText()
            assert window.centers.index.session_id == journal.session_id
            assert window.centers.data.list.topLevelItemCount() == 4
            assert window.centers.reports.list.topLevelItemCount() == 1
            window.binding.poll()
            assert window.centers.reports.list.topLevelItemCount() == 1
            assert window.centers.audit.list.topLevelItemCount() == 0
            assert not window.controller.initialize_context()
        finally:
            window.close()


def test_every_tab_bar_divides_its_width_evenly(tmp_path):
    app = QApplication.instance() or QApplication([])

    class Broker:
        def get_context(self, context):
            return context.snapshot

    with SessionJournal(tmp_path, "center-tabs") as journal:
        loop = PythonBackend(
            ScriptedProvider([]), context_tools(Broker()), journal,
            BoundContext("tabs-i", "tabs-g", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            window.show()
            bars = {
                "workspace": window.tabs.tabBar(),
                "centers": window.centers.tabs.tabBar(),
                "navigation": window.navigation.tabBar(),
            }
            assert [
                window.centers.tabText(i) for i in range(window.centers.tabs.count())
            ] == ["审阅", "汇报", "数据", "进程", "终端"]
            assert window.navigation.tabText(0) == "会话历史"
            assert window.navigation.tabText(1).startswith("任务队列")
            for total in (1100, 1280, 1600):
                window.resize(total, 820)
                for _ in range(3):
                    app.processEvents()
                for name, bar in bars.items():
                    widths = [bar.tabRect(i).width() for i in range(bar.count())]
                    assert max(widths) - min(widths) <= 1, (name, widths)
                    assert sum(widths) == bar.width(), (name, widths, bar.width())
                    assert bar.width() >= bar.parentWidget().width() - 4, name
        finally:
            window.close()

    journal, service = SessionJournal(tmp_path, "tool-tabs"), WorkbenchService()
    results = ToolResults(partial(service.tool_result, journal))
    try:
        results.resize(600, 500)
        results.show()
        for _ in range(3):
            app.processEvents()
        bar = results.details.tabBar()
        assert [
            results.details.tabText(i) for i in range(results.details.count())
        ] == ["结果表格", "数据明细", "原理图预览"]
        widths = [bar.tabRect(i).width() for i in range(bar.count())]
        assert max(widths) - min(widths) <= 1, widths
        assert sum(widths) == bar.width(), (widths, bar.width())
    finally:
        results.close()
        service.close()
        assert service.wait(5)
        journal.close()
