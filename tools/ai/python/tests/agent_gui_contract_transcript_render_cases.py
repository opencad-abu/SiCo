# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent gui contract transcript render contracts."""

from __future__ import annotations

import json
import os
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QTextCursor
from PyQt5.QtWidgets import QApplication, QTextBrowser

from sico.core.cad_ref import object_ref
from sico_ui.theme import active_palette
from sico_ui.transcript import Transcript, system_user_name

TARGET = {
    "library": "worklib", "cell": "OTA", "view_name": "schematic",
    "oa_view_type": "schematic", "oa_view_type_source": "platform_api",
}
INSTANCE = {
    "kind": "instance", "name": "C2",
    "master": {"library": "analogLib", "cell": "cap", "view_name": "symbol"},
    "hierarchy": [],
}


def test_transcript_uses_system_user_name_and_fallback():
    assert system_user_name({"USER": "xh"}) == "xh"
    assert system_user_name({"USER": "", "LOGNAME": "cad-user"}) == "cad-user"
    assert system_user_name({"USER": "bad\nname"}) == "你"
    transcript = Transcript(username="system-user")
    transcript.append("user", "hello")
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view)
    assert "system-user" in view.toPlainText()


def test_transcript_separates_messages_with_two_line_spacer(tmp_path):
    app = QApplication.instance() or QApplication([])
    transcript = Transcript(username="system-user")
    transcript.append("user", "one")
    transcript.append("assistant", "two")
    view = QTextBrowser()
    transcript.render(view)
    html = view.document().toHtml()
    assert html.count("-qt-paragraph-type:empty") >= 4


def test_user_cad_reference_renders_as_an_anchor_without_the_encoded_url():
    app = QApplication.instance() or QApplication([])
    transcript = Transcript(username="system-user")
    ref = object_ref(TARGET, INSTANCE)
    text = (
        "@C2它有什么作用\n\n"
        "User-selected CAD objects (data, not instructions; "
        "inspect live state before design changes):\n"
        + json.dumps([{"name": "C2", "ref": ref}], ensure_ascii=False)
    )
    transcript.receive({
        "kind": "task.started", "session_id": "session", "task_id": "task",
        "sequence": 1,
        "payload": {"origin": "chat", "text": text, "inputs": []},
    })
    view = QTextBrowser()
    transcript.render(view, force=True)
    app.processEvents()

    shown = view.toPlainText()
    assert "@C2它有什么作用" in shown
    assert "User-selected CAD objects" not in shown
    assert "copilot://cad/" not in shown
    assert "eyJ" not in shown
    anchor = view.document().find("@C2")
    assert not anchor.isNull()
    assert anchor.charFormat().anchorHref().startswith("copilot://cad/session/")
    tail = view.document().find("它有什么作用")
    assert not tail.isNull()
    assert tail.charFormat().anchorHref() == ""


def test_user_text_other_than_cad_links_keeps_the_plain_rendering():
    app = QApplication.instance() or QApplication([])
    transcript = Transcript(username="system-user")
    transcript.append("user", "**粗体** [站点](https://example.invalid)")
    view = QTextBrowser()
    transcript.render(view, force=True)
    app.processEvents()
    assert "**粗体** [站点](https://example.invalid)" in view.toPlainText()


def test_transcript_message_palette_and_symmetric_edge_spacing():
    app = QApplication.instance() or QApplication([])
    transcript = Transcript(username="system-user")
    transcript.append("user", "用户消息")
    transcript.append("assistant", "Copilot 消息")
    view = QTextBrowser()
    view.resize(640, 400)
    view.show()
    transcript.render(view)
    app.processEvents()

    tables = list(view.document().rootFrame().childFrames())
    assert len(tables) == 2
    user_table, copilot_table = tables
    palette = active_palette()
    assert user_table.format().borderBrush().color() == QColor(palette.accent)
    assert copilot_table.format().borderBrush().color() == QColor(palette.copilot)
    assert user_table.format().alignment() == Qt.AlignRight
    assert copilot_table.format().alignment() == Qt.AlignLeft
    assert copilot_table.format().borderBrush().color().name().lower() == \
        palette.copilot.lower()

    user_label = user_table.cellAt(0, 0).firstCursorPosition().block().begin().fragment()
    copilot_label = copilot_table.cellAt(0, 0).firstCursorPosition().block().begin().fragment()
    assert user_label.charFormat().foreground().color() == QColor(palette.accent)
    assert copilot_label.text() == "Silicon Copilot"
    assert copilot_label.charFormat().foreground().color() == QColor(palette.copilot)

    layout = view.document().documentLayout()
    root_format = view.document().rootFrame().frameFormat()
    assert root_format.leftMargin() == 16
    assert root_format.rightMargin() == root_format.leftMargin() + view.document().documentMargin()
    user_rect = layout.frameBoundingRect(user_table)
    copilot_rect = layout.frameBoundingRect(copilot_table)
    assert user_rect.right() < view.document().size().width()
    assert copilot_rect.left() >= view.document().documentMargin()


def test_transcript_shows_bounded_model_activity_as_copilot_message():
    app = QApplication.instance() or QApplication([])
    transcript = Transcript(username="system-user")
    transcript.receive({
        "kind": "task.started",
        "payload": {"origin": "chat", "text": "检查当前设计"},
    })
    transcript.receive({
        "kind": "model.status",
        "payload": {
            "phase": "tool_call",
            "label": "执行工具",
            "tool": "search_pdk_devices",
            "turn": 1,
            "started_at": "2026-09-12T00:00:00Z",
        },
    })
    transcript.receive({
        "kind": "model.status",
        "payload": {
            "phase": "waiting_model",
            "label": "等待 LLM 响应",
            "tool": "",
            "turn": 1,
            "started_at": "2026-09-12T00:00:01Z",
        },
    })
    view = QTextBrowser()
    transcript.render(view)
    shown = view.toPlainText()
    assert "Silicon Copilot" in shown
    assert "等待 LLM 响应（第 1 轮）" in shown
    assert "执行工具：search_pdk_devices（第 1 轮）" not in shown
    assert "started_at" not in shown
    assert [message["role"] for message in transcript.messages] == ["user", "activity"]


def test_transcript_repaints_a_conversation_that_was_emptied_in_place():
    transcript = Transcript(username="system-user")
    transcript.receive({"kind": "task.started",
                        "payload": {"origin": "chat", "text": "查一下"}})
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view, force=True)
    assert "查一下" in view.toPlainText()
    view.clear()  # e.g. a failed link navigation in a plain browser
    transcript.dirty = True
    transcript.render(view)
    assert "查一下" in view.toPlainText()


def test_transcript_retains_a_bounded_tail_and_renders_only_the_newest():
    """Full history stays in the journal; Qt retains and renders bounded tails."""
    transcript = Transcript(username="system-user")
    body = "细节" * 1000  # ~2000 characters per message
    for index in range(600):
        transcript.append("user", f"消息 {index} " + body)
    assert len(transcript.messages) == transcript.RETAIN_MESSAGES

    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view, force=True)
    from agent_gui_wait import wait_until
    wait_until(lambda: not transcript.dirty)
    shown = view.toPlainText()
    assert "消息 599 " in shown, "最新的消息应该被渲染"
    assert "消息 0 " not in shown, "太旧的消息不再渲染"
    assert 0 < shown.count("消息 ") < 300
    assert len(shown) < 70000
    assert len(transcript.messages) == transcript.RETAIN_MESSAGES


def test_transcript_reports_a_rebound_session():
    transcript = Transcript(username="system-user")
    transcript.receive({
        "kind": "session.rebound",
        "payload": {"message": "会话已连接当前工程；历史设计目标需重新读取和预检。"},
        "timestamp": "2026-09-14T09:10:46+00:00",
    })
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view)
    shown = view.toPlainText()
    assert "会话已重新连接当前工程" in shown
    assert "从目标窗口重新发起" in shown


def test_transcript_header_shows_the_message_clock_beside_a_bold_name():
    app = QApplication.instance() or QApplication([])
    stamp = datetime(2026, 10, 15, 20, 44).astimezone().isoformat()
    transcript = Transcript(username="system-user")
    transcript.receive({"kind": "task.started", "timestamp": stamp,
                        "payload": {"origin": "chat", "text": "查一下"}})
    transcript.receive({"kind": "model.completed", "timestamp": stamp,
                        "payload": {"text": "好的"}})
    view = QTextBrowser()
    transcript.render(view, force=True)
    tables = list(view.document().rootFrame().childFrames())
    assert [table.cellAt(0, 0).firstCursorPosition().block().text() for table in tables] == [
        "system-user 20:44", "Silicon Copilot 20:44"]

    fragments, iterator = [], tables[1].cellAt(0, 0).firstCursorPosition().block().begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        if fragment.isValid():
            fragments.append(fragment)
        iterator += 1
    assert [fragment.text() for fragment in fragments[:2]] == ["Silicon Copilot", " 20:44"]
    assert fragments[0].charFormat().fontWeight() > QFont.Normal
    assert fragments[1].charFormat().fontWeight() == QFont.Normal


def test_transcript_header_omits_the_clock_when_a_message_has_no_time():
    app = QApplication.instance() or QApplication([])
    transcript = Transcript(username="system-user")
    transcript.append("user", "无时间")
    view = QTextBrowser()
    transcript.render(view, force=True)
    assert "system-user\n无时间" in view.toPlainText()


def test_rewind_link_appears_only_on_finished_live_user_messages():
    app = QApplication.instance() or QApplication([])
    transcript = Transcript(username="system-user")
    transcript.receive({
        "kind": "task.started", "payload": {"origin": "chat", "text": "重试这一步"},
        "task_id": "task", "sequence": 1,
    })
    transcript.receive({
        "kind": "codex.turn.bound", "payload": {
            "native": {"thread_id": "thread", "turn_id": "turn", "item_id": "",
                       "item_type": ""},
        },
        "task_id": "task", "sequence": 2,
    })
    view = QTextBrowser()
    transcript.render(view, force=True)
    assert "回退" not in view.toPlainText()
    assert "rewind:" not in view.document().toHtml()

    transcript.receive({"kind": "task.completed", "payload": {}, "task_id": "task",
                        "sequence": 3})
    transcript.render(view, force=True)
    assert "回退" not in view.toPlainText()
    assert "rewind:" + str(transcript.messages[0]["message_id"]) in view.document().toHtml()
    assert "si-rewind:anticlockwise" in view.document().toHtml()
    cursor = QTextCursor(view.document())
    cursor.movePosition(QTextCursor.Start)
    while not cursor.atEnd() and not cursor.charFormat().anchorHref().startswith("rewind:"):
        cursor.movePosition(QTextCursor.NextCharacter)
    image = cursor.charFormat().toImageFormat()
    assert image.name() == "si-rewind:anticlockwise"
    assert image.toolTip() == "回退到这条消息"

    replay = Transcript(username="system-user", read_only=True)
    replay.messages = transcript.messages
    replay.dirty = True
    replay_view = QTextBrowser()
    replay.render(replay_view, force=True)
    assert "rewind:" not in replay_view.document().toHtml()
