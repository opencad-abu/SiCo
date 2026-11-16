# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent gui contract tool render contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_gui_contract_support import (
    COLLAPSED_ICON,
    COUNTER_MARK,
    EXPANDED_ICON,
    icon_point,
    link_point,
)
from agent_gui_wait import settle
from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QTextCursor, QTextTable
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QTextBrowser

from sico.core.contracts import BoundContext, ToolResult
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal
from sico_ui.theme import active_palette
from sico_ui.transcript import COUNTER_ICON, Transcript


def test_tool_counter_render_separates_tools_from_the_model_answer():
    transcript = Transcript(username="system-user")
    def receive(kind, payload, **extra):
        transcript.receive({"kind": kind, "task_id": "task", "payload": payload, **extra})

    receive("task.started", {"origin": "chat", "text": "查一下"})
    receive("tool.started", {"id": "call-1", "name": "lookup"})
    receive("tool.finished", {"id": "call-1", "name": "lookup",
                              "result": {"status": "ok", "summary": "done"}})
    receive("model.delta", {"text": "阅读完成。"})
    receive("model.completed", {"text": "阅读完成。"})
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view)
    shown = view.toPlainText()
    # “工具”二字是自绘的锤子扳手图标：文内只留计数徽记。
    assert COUNTER_MARK in view.toHtml()
    assert "\ufffc×1 · 成功 1" in shown
    assert "工具" not in shown
    assert "阅读完成。" in shown
    # only the model answer carries the assistant header
    assert shown.count("Silicon Copilot") == 1


def test_tool_counter_is_plain_text_at_half_size_with_a_toggle():
    transcript = Transcript(username="system-user")
    transcript.receive({"kind": "task.started",
                        "payload": {"origin": "chat", "text": "查一下"}})
    transcript.receive({"kind": "tool.started", "payload": {"id": "call-1", "name": "lookup"}})
    transcript.receive({"kind": "tool.finished",
                        "payload": {"id": "call-1", "name": "lookup",
                                    "result": {"status": "ok", "summary": "已读取"}}})
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view)
    document = view.document()
    shown = document.toPlainText()
    # 计数徽记是半号字；“工具”二字和折叠标记都是应用自绘的图片，不是字符。
    assert COUNTER_MARK in document.toHtml()
    assert COLLAPSED_ICON in document.toHtml()
    assert "\ufffc×1 · 成功 1" in shown
    assert str(document.find("\ufffc").charFormat().toImageFormat().name()) == COUNTER_ICON
    # The counter is not a bubble: only the user message keeps a table frame.
    tables = [frame for frame in document.rootFrame().childFrames()
              if isinstance(frame, QTextTable)]
    assert len(tables) == 1
    base_point = document.defaultFont().pointSize()
    if base_point <= 0:
        base_point = view.font().pointSize()
    if base_point <= 0:
        base_point = 10
    cursor = QTextCursor(document)
    start = shown.index("×1")
    cursor.setPosition(start)
    cursor.setPosition(start + 2, QTextCursor.KeepAnchor)
    assert cursor.charFormat().fontPointSize() == min(base_point, max(9, base_point // 2))


def test_tool_counter_expands_and_collapses_the_receipt_details():
    transcript = Transcript(username="system-user")
    def receive(kind, payload, **extra):
        transcript.receive({"kind": kind, "task_id": "task", "payload": payload, **extra})

    receive("task.started", {"origin": "chat", "text": "查一下"})
    receive("tool.started", {"id": "call-1", "name": "lookup"})
    receive("tool.finished", {"id": "call-1", "name": "lookup",
                              "result": {"status": "ok", "summary": "已读取"}},)
    receive("tool.started", {"id": "call-2", "name": "prepare_pdk_data"})
    receive("tool.finished", {"id": "call-2", "name": "prepare_pdk_data",
                              "result": {"status": "tool_error", "summary": "准备失败"}})
    receive("model.delta", {"text": "结束。"})
    group = transcript.messages[-2]
    assert group["role"] == "tools" and group["expanded"] is False
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view)
    collapsed = view.toPlainText()
    # Successful calls stay hidden until the counter is expanded.
    assert "lookup · 成功" not in collapsed
    assert "prepare_pdk_data · 执行失败 · 准备失败" in collapsed
    assert COLLAPSED_ICON in view.document().toHtml()

    assert transcript.toggle_tools(str(group["message_id"])) is True
    transcript.render(view, force=True)
    expanded = view.toPlainText()
    assert group["expanded"] is True
    assert EXPANDED_ICON in view.document().toHtml()
    assert "lookup · 成功 · 已读取" in expanded
    assert "prepare_pdk_data · 执行失败 · 准备失败" in expanded

    assert transcript.toggle_tools(str(group["message_id"])) is True
    transcript.render(view, force=True)
    assert group["expanded"] is False
    assert COLLAPSED_ICON in view.document().toHtml()
    assert transcript.toggle_tools("9999") is False


def test_failed_tool_receipt_is_red_and_references_its_data_entry():
    transcript = Transcript(username="system-user")
    def receive(kind, payload, **extra):
        transcript.receive({"kind": kind, "task_id": "task", "payload": payload, **extra})

    receive("task.started", {"origin": "chat", "text": "跑一遍"}, sequence=1)
    receive("tool.started", {"id": "call-1", "name": "lookup"}, sequence=2)
    receive("tool.finished", {"id": "call-1", "name": "lookup",
                              "result": {"status": "ok", "summary": "已读取"}}, sequence=3)
    receive("tool.started", {"id": "call-2", "name": "inspect_schematic"}, sequence=4)
    receive("tool.finished", {"id": "call-2", "name": "inspect_schematic",
                              "result": {"status": "needs_reconcile", "summary": "结果未确认"}},
            sequence=5)
    group = transcript.messages[-1]
    assert transcript.tool_data_key(group["items"][0]) == "d_3_tool"
    assert transcript.head_tool_key(str(group["message_id"])) == "d_5_tool"

    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view)
    document = view.document()
    failure = document.find("inspect_schematic · 需核对 · 结果未确认")
    assert failure.charFormat().foreground().color().name() == \
        active_palette().error.lower()
    assert failure.charFormat().anchorHref() == "tools-data:d_5_tool"
    # 计数徽记连同它前面的图标，都是这批未决问题的跳转目标。
    head = "tools-head:" + str(group["message_id"])
    assert document.find("×2").charFormat().anchorHref() == head
    assert document.find("\ufffc").charFormat().anchorHref() == head
    # A green batch still resolves to its first call when double-clicked.
    transcript.toggle_tools(str(group["message_id"]))
    transcript.render(view, force=True)
    expanded = view.toPlainText()
    assert "lookup · 成功 · 已读取" in expanded
    detail = view.document().find("inspect_schematic · 需核对 · 结果未确认")
    assert detail.charFormat().anchorHref() == "tools-data:d_5_tool"
    assert detail.charFormat().foreground().color().name() == \
        active_palette().error.lower()


def test_window_tool_counter_link_toggles_the_receipt_details(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "tool-toggle") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("toggle-instance", "toggle-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            settle(window)
            for kind, payload in (
                ("task.started", {"origin": "chat", "text": "查一下"}),
                ("tool.started", {"id": "call-1", "name": "lookup"}),
                ("tool.finished", {"id": "call-1", "name": "lookup",
                                   "result": {"status": "ok", "summary": "已读取"}}),
            ):
                window.presentation.transcript.receive({"kind": kind, "task_id": "task", "payload": payload})
            window.show()
            window.renderer.flush()
            app.processEvents()
            shown = window.display.toPlainText()
            assert COLLAPSED_ICON in window.display.toHtml() and "lookup · 成功" not in shown
            group = [m for m in window.presentation.transcript.messages if m["role"] == "tools"][-1]
            QTest.mouseClick(window.display.viewport(), Qt.LeftButton, Qt.NoModifier,
                             icon_point(window.display))
            app.processEvents()
            assert group["expanded"] is True
            expanded = window.display.toPlainText()
            # A link click must never blank the transcript.
            assert "查一下" in expanded
            assert EXPANDED_ICON in window.display.toHtml()
            assert "lookup · 成功 · 已读取" in expanded
            QTest.mouseClick(window.display.viewport(), Qt.LeftButton, Qt.NoModifier,
                             icon_point(window.display))
            app.processEvents()
            assert group["expanded"] is False
            assert COLLAPSED_ICON in window.display.toHtml()
        finally:
            window.close()


def test_history_replay_tool_counter_toggles_the_read_only_transcript(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "replay-toggle") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("replay-instance", "replay-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            replay = Transcript(read_only=True)
            for kind, payload in (
                ("task.started", {"origin": "chat", "text": "查一下"}),
                ("tool.started", {"id": "call-1", "name": "lookup"}),
                ("tool.finished", {"id": "call-1", "name": "lookup",
                                   "result": {"status": "ok", "summary": "已读取"}}),
            ):
                replay.receive({"kind": kind, "task_id": "task", "payload": payload})
            window.page.reviewing = "history"
            window.presentation.replay = replay
            window.display.clear()
            replay.render(window.display, force=True)
            group = replay.messages[-1]
            window.display.anchorClicked.emit(QUrl("tools:" + str(group["message_id"])))
            assert group["expanded"] is True
            shown = window.display.toPlainText()
            assert EXPANDED_ICON in window.display.toHtml()
            assert "lookup · 成功 · 已读取" in shown
            # The live transcript of the window stays untouched by the replay toggle.
            assert window.presentation.transcript.messages == []
            app.processEvents()
        finally:
            window.close()


def test_double_click_on_a_failed_receipt_opens_its_recorded_data_entry(tmp_path):
    from test_agent_workbench import backend_for, begin, call

    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "tool-jump") as journal:
        backend = backend_for(journal)
        begin(backend)
        reply = ToolResult("needs_reconcile", "结果未确认")
        call(backend, "inspect_schematic", {"cellview": "amp"}, result=reply)
        window = AssistantWindow(backend)
        try:
            window.show()
            settle(window)
            app.processEvents()
            shown = window.display.toPlainText()
            assert COUNTER_MARK in window.display.toHtml() and "\ufffc×1" in shown
            assert "需核对 1" in shown
            assert window.tabs.currentWidget() is not window.detail
            point = link_point(window.display, "inspect_schematic · 需核对 · 结果未确认")
            # A receipt line only reacts to the double-click gesture.
            QTest.mouseClick(window.display.viewport(), Qt.LeftButton, Qt.NoModifier, point)
            app.processEvents()
            assert window.tabs.currentWidget() is not window.detail
            QTest.mouseDClick(window.display.viewport(), Qt.LeftButton, Qt.NoModifier, point)
            settle(window)
            app.processEvents()
            sequence = next(event["sequence"] for event in journal.events()
                            if event["kind"] == "tool.finished")
            key = f"d_{sequence}_tool"
            assert window.centers.tabs.currentWidget() is window.centers.data
            selected = window.centers.data.list.currentItem()
            assert selected is not None
            assert selected.data(0, Qt.UserRole)["key"] == key
            # The detail page shows the recorded call as well.
            assert window.tabs.currentWidget() is window.detail
            assert window.detail.current == ("data", key)
        finally:
            window.close()
