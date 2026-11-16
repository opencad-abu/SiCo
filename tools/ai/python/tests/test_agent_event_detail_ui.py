"""Verified structured records are readable in live and history detail dialogs."""

from concurrent.futures import Future

import pytest
from agent_gui_wait import wait_until
from agent_replay_fixtures import record_task
from agent_worker_fixtures import window as window
from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton, QTextBrowser
from test_agent_event_content import visible
from test_agent_event_publication import next_batch, stream_for

from sico.service.event_content import detail_page
from sico_ui.event_detail import show_event_detail
from sico_ui.presentation import document_tables
from sico_ui.receipts import DataReceipt
from sico_ui.session_presentation import SessionPresentation


def test_verified_structured_record_can_be_read_through_service(window):
    controller = window.controller
    _, state = record_task(controller.journal, controller.current, "tool-task", terminal=False)
    output = "完整执行输出\n" * 900
    controller.journal.append("tool.finished", {"id": "call", "name": "codex_command",
        "result": {"status": "ok", "data": {"output": output}}}, state)
    stream = stream_for(window)
    batch = next_batch(window, stream)
    event = next(row for row in batch.events if row["kind"] == "tool.finished")
    offset, pages = 0, []
    while True:
        page = window.sessions.worker.event_detail(stream, event["detail"], offset).result(5)
        assert page["title"] == "工具结果 · 命令执行"
        pages.append(page["text"])
        if page["next"] == page["total"]:
            break
        offset = page["next"]
    text = visible("".join(pages))
    assert "状态成功（ok）" in text and output in text
    assert '"data":' not in text


@pytest.mark.parametrize("history", [False, True])
def test_dialog_automatically_expands_full_message(window, monkeypatch, history):
    if history:
        window.page.preview_session("history")
        wait_until(lambda: not window.page.opening)
    payload = {"name": "codex_command", "result": {"status": "failed", "data": {
        "output": '<b>这是原始输出</b>\n<img src="file:///tmp/private">\n' + "长输出\n" * 2500,
        "error": "末尾错误信息",
    }}}
    raw = {"kind": "tool.finished", "payload": payload}
    calls, futures = [], []

    def read(feed, key, offset):
        calls.append((feed, key, offset))
        future = Future()
        future.set_result(detail_page(raw, offset, 4096))
        futures.append(future)
        return future

    monkeypatch.setattr(window.api, "event_detail", read)
    show_event_detail(window, "readable-record")
    receipt = next(r for r in window.findChildren(DataReceipt) if r.pending
                   and r.pending[0] is futures[0])
    dialog = receipt.parent()
    try:
        receipt.poll()
        view = dialog.findChild(QTextBrowser)
        expected_stream = (window.page_presentation.delivery.stream if history
                           else window.binding.delivery.stream)
        assert calls == [(expected_stream, "readable-record", 0)]
        assert view.isReadOnly() and not view.openLinks() and not view.openExternalLinks()
        assert view.toPlainText() == ""  # Transport fragments never leak into the document.
        assert any(label.text() == "工具结果 · 命令执行" for label in dialog.findChildren(QLabel))
        buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
        assert "上一页" not in buttons and "下一页" not in buttons
        assert not buttons["复制全文"].isEnabled()
        assert dialog.isWindow() and not dialog.isModal()
        wait_until(buttons["复制全文"].isEnabled)
        complete = detail_page(raw, 0, 1000000)["text"]
        assert '<b>这是原始输出</b>' in view.toPlainText()
        assert "失败（failed）" in view.toPlainText()
        assert view.toPlainText().count("长输出") == 2500
        assert [call[2] for call in calls] == list(range(0, len(complete), 4096))
        assert "末尾错误信息" in view.toPlainText()
        assert view.verticalScrollBar().value() == 0
        clipboard = QApplication.clipboard()
        previous_text = clipboard.text()
        try:
            buttons["复制全文"].click()
            assert clipboard.text() == view.toPlainText()
        finally:
            clipboard.setText(previous_text)
    finally:
        dialog.close()


def test_dark_full_content_tables_and_output_follow_the_theme(window, monkeypatch):
    from cadgui.palette import activate
    from sico_ui.event_detail import EventDetailDialog
    from sico_ui.theme import DARK_PALETTE, LIGHT_PALETTE, active_palette

    previous = active_palette().name
    activate("dark")
    raw = {"kind": "tool.finished", "payload": {
        "name": "list_copilot_data",
        "result": {
            "status": "ok",
            "summary": "数据索引",
            "output": "",
            "data": {"rows": [{"name": "alpha", "kind": "report"}]},
        },
    }}

    def read(_feed, _key, offset):
        future = Future()
        future.set_result(detail_page(raw, offset, 4096))
        return future

    monkeypatch.setattr(window.api, "event_detail", read)
    show_event_detail(window, "dark-detail")
    dialog = window.findChild(EventDetailDialog)
    try:
        wait_until(dialog.copy.isEnabled)
        tables = document_tables(dialog.view.document())
        assert tables
        labels = [
            table.cellAt(row, 0).format().background().color().name().lower()
            for table in tables
            for row in range(table.rows())
        ]
        assert set(labels) == {DARK_PALETTE.code_block_background.lower()}
        assert LIGHT_PALETTE.code_block_background.lower() not in labels
        output = [
            (block, run)
            for block in iter_blocks(dialog.view.document())
            if "（空文本）" in block.text()
            for run in iter_fragments(block)
        ]
        assert output
        assert {run.charFormat().background().color().name().lower()
                for _block, run in output} == {
                    DARK_PALETTE.code_block_background.lower()}
        image = dialog.view.viewport().grab().toImage()
        assert not any(
            image.pixelColor(x, y).name().lower()
            == LIGHT_PALETTE.code_block_background.lower()
            for y in range(image.height())
            for x in range(image.width())
        )
    finally:
        dialog.close()
        activate(previous)


def iter_blocks(document):
    block = document.begin()
    while block.isValid():
        yield block
        block = block.next()


def iter_fragments(block):
    fragment = block.begin()
    while not fragment.atEnd():
        run = fragment.fragment()
        if run.isValid():
            yield run
        fragment += 1


def test_live_and_replay_share_one_detail_notice_owner(window):
    presentation = SessionPresentation(window.controller.current)
    event = {"kind": "model.completed", "sequence": 1,
             "payload": {"text": "聊天中的正文"}, "detail": "verified-record"}
    assert presentation.receive(event)
    assert not presentation.receive(event)
    replay = presentation.preview()
    replay.receive(event)
    for transcript in (presentation.transcript, replay):
        links = [message for message in transcript.messages if message["role"] == "native"]
        assert len(links) == 1
        assert links[0]["text"] == "完整内容"
        assert links[0]["result"] == {"url": "event-detail:verified-record"}


def test_reply_markdown_is_rendered_after_transport_chunks_are_joined(window, monkeypatch):
    from sico_ui.presentation import document_tables

    # A fence crosses the first chunk boundary; formatting individual chunks
    # would break the code block, and field text must not be parsed as markdown.
    body = "# 完整分析\n\n**结论明确**\n\n" + "分析过程。" * 815 + (
        "\n\n```python\nvalue = '<b>保持原样</b>'\nprint(value)\n```\n\n"
        "结果\n| 指标 | 数值 |\n|---|---|\n| 增益 | 42 dB |\n\n最后一句。")
    raw = {"kind": "model.completed", "payload": {"text": body, "message": "**附加原文**"}}

    def read(_feed, _key, offset):
        future = Future()
        future.set_result(detail_page(raw, offset, 4096))
        return future

    monkeypatch.setattr(window.api, "event_detail", read)
    show_event_detail(window, "markdown-reply")
    from sico_ui.event_detail import EventDetailDialog

    dialog = window.findChild(EventDetailDialog)
    try:
        wait_until(dialog.copy.isEnabled)
        text = dialog.view.toPlainText()
        assert "完整分析" in text and "# 完整分析" not in text
        assert "结论明确" in text and "**结论明确**" not in text
        assert "```python" not in text and "<b>保持原样</b>" in text
        assert "最后一句。" in text and "**附加原文**" in text
        assert len(document_tables(dialog.view.document())) == 2
        cursor = dialog.view.document().find("结论明确")
        assert cursor.charFormat().fontWeight() >= QFont.Bold
        assert not dialog.render_parts
    finally:
        dialog.close()


def test_failed_continuation_keeps_text_and_retries_at_same_offset(window, monkeypatch):
    from sico_ui.event_detail import EventDetailDialog

    body, calls = "长消息\n" * 2000 + "最后一行", []
    raw = {"kind": "task.started", "payload": {"text": body}}

    def read(_feed, _key, offset):
        calls.append(offset)
        future = Future()
        if len(calls) == 2:
            future.set_exception(ValueError("暂时断开"))
        else:
            future.set_result(detail_page(raw, offset, 4096))
        return future

    monkeypatch.setattr(window.api, "event_detail", read)
    show_event_detail(window, "retry-message")
    dialog = window.findChild(EventDetailDialog)
    try:
        wait_until(dialog.retry.isVisible)
        assert dialog.view.toPlainText() == body[:4096]
        assert not dialog.copy.isEnabled() and "尚未完整读取" in dialog.status.text()
        dialog.retry.click()
        wait_until(dialog.copy.isEnabled)
        assert calls[:3] == [0, 4096, 4096]
        assert dialog.view.toPlainText() == body
    finally:
        dialog.close()


@pytest.mark.parametrize("close", [False, True])
def test_pending_continuation_stops_on_close_or_session_change(window, monkeypatch, close):
    from sico_ui.event_detail import EventDetailDialog

    calls, pending = [], Future()
    pending.set_running_or_notify_cancel()

    def read(_feed, _key, offset):
        calls.append(offset)
        if offset:
            return pending
        future = Future()
        future.set_result({"text": "开头", "offset": 0, "next": 2, "total": 100})
        return future

    monkeypatch.setattr(window.api, "event_detail", read)
    show_event_detail(window, "late-message")
    dialog = window.findChild(EventDetailDialog)
    wait_until(lambda: calls == [0, 2])
    if close:
        dialog.close()
    else:
        window.page.preview_session("history")
    pending.set_result({"text": "迟到内容", "offset": 2, "next": 6, "total": 6})
    dialog.receipt.poll()
    assert not dialog.isVisible() and not dialog.continuation.isActive()
    assert dialog.view.toPlainText() == "开头" and calls == [0, 2]


def test_continuation_preserves_selection_and_reading_position(window, monkeypatch):
    from sico_ui.event_detail import EventDetailDialog

    futures = []

    def read(*_args):
        future = Future()
        futures.append(future)
        return future

    monkeypatch.setattr(window.api, "event_detail", read)
    show_event_detail(window, "reading-message")
    dialog = window.findChild(EventDetailDialog)
    first, last = "第一部分\n" * 200, "最后部分"
    total = len(first) + len(last)
    try:
        futures[0].set_result({"text": first, "offset": 0, "next": len(first), "total": total})
        wait_until(lambda: len(futures) == 2)
        cursor = dialog.view.textCursor()
        cursor.setPosition(2)
        cursor.setPosition(8, QTextCursor.KeepAnchor)
        dialog.view.setTextCursor(cursor)
        selected = cursor.selectedText()
        dialog.view.verticalScrollBar().setValue(50)
        position = dialog.view.verticalScrollBar().value()
        futures[1].set_result({"text": last, "offset": len(first), "next": total, "total": total})
        wait_until(dialog.copy.isEnabled)
        assert dialog.view.toPlainText() == first + last
        assert dialog.view.textCursor().selectedText() == selected
        assert dialog.view.verticalScrollBar().value() == position
    finally:
        dialog.close()
