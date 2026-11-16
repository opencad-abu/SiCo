"""Evidence links and bounded history updates keep the visible conversation stable."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from agent_gui_wait import wait_until
from PyQt5.QtCore import QPoint
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import QApplication
from sico.service.event_display import EventDisplay
from sico_ui.presentation import DocumentView
from sico_ui.theme import active_palette
from sico_ui.transcript import Transcript


@pytest.fixture
def surface():
    app = QApplication.instance() or QApplication([])
    view = DocumentView()
    view.resize(600, 350)
    view.show()
    app.processEvents()
    transcript = Transcript()
    yield transcript, view
    transcript.cancel_render()
    view.close()


def finish_render(transcript, view):
    transcript.render(view, force=True)
    wait_until(lambda: not transcript.dirty)


def append_message(transcript, index):
    # Mixed paragraph/table content exercises Qt frame boundaries on eviction.
    role = "user" if index % 2 else "assistant"
    return transcript.append(role, f"消息 {index} 🧪\n\n" + "阅读这一行。\n\n" * 5)


@pytest.mark.parametrize("width", [340, 600])
def test_short_evidence_links_keep_the_existing_conversation(surface, width):
    transcript, view = surface
    view.resize(width, 350)
    transcript.append("user", "设计要求：保持这一轮的上下文可见")
    projector = EventDisplay()
    for sequence, name in enumerate(("get_circuit_operation_schema", "search_skill_api",
                                     "Search"), 1):
        raw = {"kind": "tool.finished", "sequence": sequence, "session_id": "session",
               "task_id": "task", "timestamp": "2026-09-22T04:44:00+00:00",
               "payload": {"id": str(sequence), "name": name,
                           "result": {"status": "ok", "data": {"text": "x" * 7000}}}}
        projected, _ = projector.project(raw)
        assert projected.get("detail")
        transcript.receive(projected)
        finish_render(transcript, view)

    shown = view.toPlainText()
    assert "设计要求：保持这一轮的上下文可见" in shown
    assert shown.count("完整内容") == 3
    assert "聊天中仅展示部分内容" not in shown
    assert "x" * 100 not in shown
    html = view.document().toHtml()
    for sequence in range(1, 4):
        assert f"event-detail:e{sequence}_" in html


def test_full_content_link_uses_the_tool_counter_size(surface):
    transcript, view = surface
    transcript.append("native", "完整内容", result={"url": "event-detail:size"})
    finish_render(transcript, view)
    document = view.document()
    shown = document.toPlainText()
    assert shown.strip() == "完整内容"
    assert "聊天中仅展示部分内容" not in shown
    base_point = document.defaultFont().pointSize()
    if base_point <= 0:
        base_point = view.font().pointSize()
    if base_point <= 0:
        base_point = 10
    cursor = QTextCursor(document)
    start = shown.index("完整内容")
    cursor.setPosition(start)
    cursor.setPosition(start + len("完整内容"), QTextCursor.KeepAnchor)
    assert cursor.charFormat().fontPointSize() == min(base_point, max(9, base_point // 2))
    assert cursor.charFormat().anchorHref() == "event-detail:size"


def test_markdown_link_is_underlined_while_inline_code_is_only_highlighted(surface):
    transcript, view = surface
    transcript.append(
        "assistant",
        "参数 `inline.json`；打开 [linked.json](/tmp/local-file-links/linked.json)。",
    )
    finish_render(transcript, view)
    document = view.document()

    inline = document.find("inline.json")
    linked = document.find("linked.json")
    assert not inline.isNull() and not linked.isNull()
    inline_format = document_format(document, inline)
    linked_format = document_format(document, linked)
    assert not inline_format.isAnchor()
    assert not inline_format.fontUnderline()
    assert linked_format.isAnchor()
    assert linked_format.fontUnderline()
    assert linked_format.anchorHref() == "/tmp/local-file-links/linked.json"
    assert linked_format.foreground().color().name().lower() == active_palette().link.lower()


def document_format(document, found):
    cursor = QTextCursor(document)
    cursor.setPosition(found.selectionStart() + 1)
    return cursor.charFormat()


@pytest.mark.parametrize("width", [340, 600])
@pytest.mark.parametrize("following", [False, True])
def test_prefix_eviction_keeps_bubbles_and_reading_position(surface, width, following):
    transcript, view = surface
    view.resize(width, 350)
    transcript.RENDER_MESSAGES = 8
    for index in range(8):
        append_message(transcript, index)
    finish_render(transcript, view)
    bar = view.verticalScrollBar()
    if not following:
        found = view.document().find("消息 4")
        view.setTextCursor(found)
        view.ensureCursorVisible()
        bar.setValue(bar.value() + view.cursorRect(found).top() - 30)
    anchor = view.cursorForPosition(QPoint(0, 0))
    anchor_top = view.cursorRect(anchor).top()
    anchor_text = anchor.block().text()
    # A single-message frame must append the new tail, not rebuild old bubbles.
    transcript.RENDER_ITEMS = 1
    for index in range(8, 11):
        kept = view.document().rootFrame().childFrames()[1:]
        append_message(transcript, index)
        transcript.render(view, force=True)
        QApplication.instance().processEvents()
        assert not transcript.dirty
        assert view.document().rootFrame().childFrames()[:-1] == kept
        shown = view.toPlainText()
        assert f"消息 {index - 8} " not in shown
        assert f"消息 {index} " in shown
        assert shown.count("消息 ") == 8
        if following:
            assert bar.value() == bar.maximum()
        else:
            assert anchor.block().text() == anchor_text
            assert abs(view.cursorRect(anchor).top() - anchor_top) <= 1


def test_character_budget_eviction_keeps_mixed_bubble_boundaries(surface):
    transcript, view = surface
    transcript.RENDER_CHARS = 360
    for index in range(3):
        transcript.append("native", "证据" + str(index), result={
            "url": "event-detail:e" + str(index), "status": "已更新"})
        transcript.append("user", f"第 {index} 段 " + "字" * 90)
    finish_render(transcript, view)
    # Shift a text-budget boundary repeatedly, then edit the retained tail.
    transcript.RENDER_ITEMS = 1
    for index in range(3, 8):
        message = transcript.append("assistant", f"第 {index} 段 " + "新" * 90)
        transcript.render(view, force=True)
        assert not transcript.dirty
        assert f"第 {index} 段 " in view.toPlainText()
        message["text"] += "结尾"
        transcript._changed(message)
        transcript.render(view, force=True)
        assert not transcript.dirty
        assert view.toPlainText().count("结尾") <= 3
        assert view.toPlainText().count(f"第 {index} 段 ") == 1
        assert view.document().toPlainText().rstrip().endswith("结尾")


def test_stream_replacement_does_not_publish_a_collapsed_scroll_range(surface):
    transcript, view = surface
    for index in range(5):
        append_message(transcript, index)
    message = transcript.append("assistant", "流式内容\n\n" * 12)
    finish_render(transcript, view)
    bar = view.verticalScrollBar()
    start = bar.value()
    observed = []
    bar.valueChanged.connect(observed.append)
    for index in range(3):
        message["text"] += f"新增第 {index} 行\n\n"
        transcript._changed(message)
        transcript.render(view, force=True)
        QApplication.instance().processEvents()
        assert bar.value() == bar.maximum()
        assert min(observed, default=start) >= start


def test_replacement_without_overlap_removes_old_tables(surface):
    transcript, view = surface
    for index in range(3):
        append_message(transcript, index)
    finish_render(transcript, view)
    transcript.RENDER_MESSAGES = 1
    transcript.append("native", "唯一的新记录", result={
        "url": "event-detail:new", "status": "已更新"})
    finish_render(transcript, view)
    assert not view.document().rootFrame().childFrames()
    assert "消息 " not in view.toPlainText()
    assert "唯一的新记录 · 已更新" in view.toPlainText()
    cursor = QTextCursor(view.document())
    cursor.movePosition(QTextCursor.End)
    assert cursor.currentTable() is None
