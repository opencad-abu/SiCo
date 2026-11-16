# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent gui contract input contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_gui_wait import settle
from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import QMimeData, Qt
from PyQt5.QtGui import QTextCursor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal
from sico_ui.input import (
    SendOnReturnEdit,
    SendOnReturnLineEdit,
    SendOnReturnRadioButton,
)


def test_long_clipboard_paste_is_retained_as_a_text_attachment():
    app = QApplication.instance() or QApplication([])
    editor = SendOnReturnEdit(attachments_enabled=True)
    editor.setPlainText("保留的短草稿")
    pasted = "剪贴板长文本\n" + "内容 " * 6000
    source = QMimeData()
    source.setText(pasted)
    editor.insertFromMimeData(source)
    app.processEvents()
    assert editor.toPlainText() == "保留的短草稿"
    assert editor.text_attachment() == pasted
    editor.clear_text_attachment()
    assert editor.text_attachment() is None


def test_short_clipboard_paste_remains_inline_text():
    app = QApplication.instance() or QApplication([])
    editor = SendOnReturnEdit(attachments_enabled=True)
    source = QMimeData()
    source.setText("普通剪贴板文本")
    editor.insertFromMimeData(source)
    assert editor.toPlainText() == "普通剪贴板文本"
    assert editor.text_attachment() is None


def test_editing_visible_draft_keeps_pending_text_attachment():
    app = QApplication.instance() or QApplication([])
    editor = SendOnReturnEdit(attachments_enabled=True)
    editor.setPlainText("草稿")
    source = QMimeData()
    source.setText("x" * 20000)
    editor.insertFromMimeData(source)
    assert editor.text_attachment() is not None
    editor.insertPlainText("补充说明")
    assert editor.text_attachment() is not None


def test_target_choice_opens_the_picker_and_reports_the_target(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "target-choice") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("target-instance", "target-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            window.actions.pick_design_target = lambda completed, **scope: completed({
                "library": "AnalogWork", "cell": "amp_tb",
                "view_name": "schematic", "oa_view_type": "schematic",
                "oa_view_type_source": "platform_api",
                "read_only": False, "window": "win:7",
            })
            answers = {"target": {"choice": "新开窗口…", "text": ""}}
            resolved = []
            window.actions.resolve_target_choice(answers, resolved.append)
            assert resolved[0]["target"]["choice"] == "新开窗口…"
            assert answers["target"]["text"] == ""
            text = resolved[0]["target"]["text"]
            assert "AnalogWork / amp_tb / schematic" in text and "win:7" in text

            window.actions.pick_design_target = lambda completed, **scope: completed(None)
            window.actions.resolve_target_choice(answers, resolved.append)
            assert resolved[-1] is None

            plain = {"pdk_library": {"choice": "smic28hkmg", "text": ""}}
            window.actions.resolve_target_choice(plain, resolved.append)
            assert resolved[-1] == plain
        finally:
            window.close()


def test_cancelled_target_picker_does_not_submit_the_audit(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "target-cancel") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("cancel-instance", "cancel-generation", "bound", {"valid": True}),
        )
        window = AssistantWindow(loop)
        try:
            window.presentation.state = {"task": {"id": "task-1"}}
            window.actions.pick_design_target = lambda completed, **scope: completed(None)
            commands = []
            window.actions.run_command = lambda *args, **kwargs: commands.append(args)
            window.actions.answer_audit("audit-1", {"target": {"choice": "新开窗口…", "text": ""}})
            assert commands == []
        finally:
            window.close()


def test_main_input_enter_sends_and_shift_enter_inserts_newline(tmp_path):
    _app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "enter-send") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("enter-instance", "enter-generation", "bound", {"valid": True}),
        )
        loop._event("session.created", {"entry": "test"})
        window = AssistantWindow(loop)
        settle(window)
        submitted = []
        window.controller.submit = lambda text, **options: submitted.append(text)
        window.input.setPlainText("第一行")
        window.input.moveCursor(QTextCursor.End)
        try:
            QTest.keyClick(window.input, Qt.Key_Return, Qt.ShiftModifier)
            assert window.input.toPlainText() == "第一行\n"
            assert submitted == []

            QTest.keyClick(window.input, Qt.Key_Return)
            settle(window)
            assert submitted == ["第一行"]
        finally:
            window.close()


def test_return_submit_inputs_share_one_enter_rule():
    """单行输入框和选项圆点：回车提交，Shift+回车不提交。"""
    _app = QApplication.instance() or QApplication([])
    for widget in (SendOnReturnLineEdit(), SendOnReturnRadioButton()):
        submitted = []
        widget.submitted.connect(lambda: submitted.append(True))
        QTest.keyClick(widget, Qt.Key_Return, Qt.ShiftModifier)
        assert submitted == []
        QTest.keyClick(widget, Qt.Key_Return)
        assert submitted == [True]


def test_session_input_border_follows_input_availability(tmp_path):
    """输入框边框：可以打字（聚焦）时粗绿线，其余（未聚焦/禁用/只读）细灰白线。"""
    from sico_ui.theme import active_palette

    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "input-border") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("border-instance", "border-generation", "bound", {"valid": True}),
        )
        loop._event("session.created", {"entry": "test"})
        window = AssistantWindow(loop)
        palette = active_palette()
        try:
            editor = window.input
            # 样式只认窗口命名的这一个输入框，快速输入框共用同一组规则。
            assert editor.objectName() == "sessionInput"
            stylesheet = window.styleSheet()
            base_rule = stylesheet.split("QPlainTextEdit#sessionInput,", 1)[1].split("}", 1)[0]
            assert "QPlainTextEdit#quickRequest" in base_rule
            assert f"border: 1px solid {palette.input_blocked}" in base_rule
            # 只有聚焦态是粗绿线：全局 focus 规则否则会把它拉回红棕色。
            focus_rule = stylesheet.split("QPlainTextEdit#sessionInput:focus,", 1)[1].split(
                "}", 1
            )[0]
            assert f"border: 2px solid {palette.input_ready}" in focus_rule

            def border():
                app.processEvents()
                image = editor.grab().toImage()
                row = image.height() // 2
                return [image.pixelColor(x, row).name().upper() for x in range(2)]

            window.show()
            window.activateWindow()
            editor.setFocus()
            settle(window)
            # 聚焦且可以打字：粗绿线。
            assert border() == [palette.input_ready, palette.input_ready]
            # 聚焦但不能打字（例如选中了其它会话）：粗灰白线。
            editor.setReadOnly(True)
            app.processEvents()
            assert border() == [palette.input_blocked, palette.input_blocked]
            editor.setReadOnly(False)
            # 焦点移走：细灰白线。
            window.display.setFocus()
            app.processEvents()
            assert border() == [palette.input_blocked, palette.background]
            editor.setEnabled(False)
            assert border() == [palette.input_blocked, palette.background]
        finally:
            window.close()


def test_offsession_selection_makes_the_input_read_only(window):
    """单击非现场会话：输入框只读（可聚焦、不能打字、不能提交），激活后恢复输入。"""
    from agent_navigation_contract_support import _item, _tick

    window.navigation.select(_item(window, "history"))
    _tick(window)
    assert window.navigation.selected_id == "history"
    # 仍可聚焦（粗灰线），但不能打字，也不能提交。
    assert window.input.isEnabled() and window.input.isReadOnly()
    assert window.presentation.state
    assert not window.steering_input.available()

    # 单击现场会话行：恢复可输入。
    window.navigation.select(_item(window, "initial"))
    _tick(window)
    assert not window.input.isReadOnly()
    assert window.steering_input.available()

    # 选中历史记录后激活它：选中清空，恢复可输入。
    window.navigation.select(_item(window, "history"))
    _tick(window)
    assert window.input.isReadOnly()
    window.navigation._continue_session("history")
    settle(window)
    assert window.controller.session_id == "history"
    assert not window.input.isReadOnly()
