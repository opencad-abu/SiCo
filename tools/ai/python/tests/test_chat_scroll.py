"""Scrolling pages retain reading anchors while the live stream advances."""

import os
from concurrent.futures import Future

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from agent_gui_wait import settle, wait_until
from agent_replay_fixtures import record_task
from agent_window_fixture import create_window
from sico.core.contracts import BoundContext, ModelTurn, TextDelta
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.service.event_display import encoded
from sico.storage.journal import SessionJournal
from PyQt5.QtCore import QPoint
from PyQt5.QtWidgets import QApplication


@pytest.fixture
def window(tmp_path):
    context = BoundContext('instance', 'generation', 'target', {})
    with SessionJournal(tmp_path, 'scroll-history') as journal:
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, context)
        _, state = record_task(journal, context, 'first-question', terminal=False)
        for index in range(625):
            if index % 10 == 0:
                payload = {'id': f'call-{index}', 'name': 'read', 'input': {}}
                journal.append('tool.started', payload, state)
                journal.append('tool.finished', {**payload, 'result': {'status': 'ok'}}, state)
            journal.append('model.completed', {
                'text': f'answer {index:04d} 🧪\n\nparagraph\n\n| A | B |\n| - | - |\n| 1 | 2 |',
            }, state)
        state.task['status'] = 'completed'
        journal.append('task.completed', {}, state)
        widget = create_window(loop)
        widget.resize(900, 650)
        widget.show()
        settle(widget)
        wait_until(lambda: not widget.presentation.transcript.dirty)
        try:
            yield widget
        finally:
            widget.chat_scroll.reset()
            widget.sessions.close()
            assert widget.sessions.wait(5)
            widget.close()


def page_at_edge(window, direction):
    scroll, bar = window.chat_scroll, window.display.verticalScrollBar()
    bar.setValue(0 if direction == 'before' else bar.maximum())
    scroll.load(direction)
    wait_until(lambda: scroll.receipt.pending is None)
    assert not scroll.error
    wait_until(lambda: not scroll.history.dirty)
    assert len(scroll.history.messages) <= scroll.CACHE_ROWS
    assert sum(len(encoded(row)) for row in scroll.history.messages) <= scroll.CACHE_BYTES


@pytest.mark.parametrize('width', [640, 1000])
def test_scroll_to_first_message_then_reload_evicted_forward_pages(window, width):
    window.resize(width, 650)
    QApplication.instance().processEvents()
    scroll = window.chat_scroll
    seen = set()
    for _ in range(30):
        page_at_edge(window, 'before')
        seen.update(row['text'].splitlines()[0] for row in scroll.history.messages)
        if not scroll.older:
            break
    assert not scroll.older
    assert 'first-question' in window.display.toPlainText()
    assert scroll.newer
    for _ in range(30):
        page_at_edge(window, 'after')
        seen.update(row['text'].splitlines()[0] for row in scroll.history.messages)
        if not scroll.newer:
            break
    assert not scroll.newer
    assert 'answer 0624' in window.display.toPlainText()
    assert {f'answer {i:04d} 🧪' for i in range(625)} <= seen
    keys = [row['message_id'] for row in scroll.history.messages]
    assert len(set(keys)) == len(keys)
    window.latest.click()
    assert scroll.history is None
    bar = window.display.verticalScrollBar()
    assert bar.value() == bar.maximum()


@pytest.mark.parametrize('width', [640, 1000])
def test_prepend_preserves_frames_text_anchor_and_tool_links(window, width):
    window.resize(width, 650)
    app = QApplication.instance()
    app.processEvents()
    scroll, view = window.chat_scroll, window.display
    bar = view.verticalScrollBar()
    # Freeze well away from automatic prefetch, then explicitly request a page.
    bar.setValue(200)
    assert scroll.history is not None
    anchor = view.cursorForPosition(QPoint(0, 0))
    top, text = view.cursorRect(anchor).top(), anchor.block().text()
    frames = view.document().rootFrame().childFrames()
    scroll.load('before')
    wait_until(lambda: scroll.receipt.pending is None)
    assert not scroll.error
    assert anchor.block().text() == text
    assert abs(view.cursorRect(anchor).top() - top) <= 1
    assert all(frame in view.document().rootFrame().childFrames() for frame in frames[:5])
    shown = view.toPlainText()
    ids = [row['message_id'] for row in scroll.history.messages]
    assert ids == sorted(ids)
    assert shown.index(scroll.history.messages[0]['text'].splitlines()[0]) < shown.index(text)
    tool = next(row for row in scroll.history.messages[:40] if row['role'] == 'tools')
    assert f'tools:{tool["message_id"]}' in view.document().toHtml()
    window.details.toggle_tool_details(str(tool['message_id']))
    wait_until(lambda: not scroll.history.dirty)
    assert tool['expanded']
    html = view.document().toHtml()
    assert f'tools-data:d_{tool["items"][0]["sequence"]}_tool' in html
    assert anchor.block().text() == text
    assert abs(view.cursorRect(anchor).top() - top) <= 1



def test_live_events_do_not_replace_pinned_reading_window(window):
    page_at_edge(window, 'before')
    scroll, view = window.chat_scroll, window.display
    anchor = view.cursorForPosition(QPoint(0, 0))
    top, text, content = view.cursorRect(anchor).top(), anchor.block().text(), view.toPlainText()
    record_task(window.controller.journal, window.controller.current, 'new-live-question')
    end = len(window.controller.journal.records)
    wait_until(lambda: window.binding.delivery.cursor.sequence == end)
    window.renderer.flush()
    assert view.toPlainText() == content
    assert anchor.block().text() == text
    assert view.cursorRect(anchor).top() == top
    assert window.latest.isEnabled()
    assert '有新消息' in window.latest.toolTip()
    window.latest.click()
    wait_until(lambda: not window.presentation.transcript.dirty)
    assert 'new-live-question' in view.toPlainText()
    assert scroll.history is None


def test_submitting_returns_a_frozen_reader_to_the_live_tail(window):
    """发送消息把翻阅历史时的冻结视图带回实时尾部：新回合的输入与回答立即可见。"""
    scroll, view = window.chat_scroll, window.display
    bar = view.verticalScrollBar()
    bar.setValue(200)
    assert scroll.history is not None
    window.controller.loop.provider = ScriptedProvider(
        [[TextDelta("新回合回答"), ModelTurn("新回合回答")]]
    )
    window.input.setPlainText("继续检查原理图")
    window.actions.submit()
    settle(window)
    assert scroll.history is None
    assert bar.value() == bar.maximum()
    shown = view.toPlainText()
    assert "继续检查原理图" in shown and "新回合回答" in shown


def test_submitting_returns_a_stale_unfrozen_view_to_the_live_tail(window):
    """会话激活留下的旧滚动位置不能挡住本回合的新气泡。"""

    scroll, view = window.chat_scroll, window.display
    bar = view.verticalScrollBar()
    scroll.reset()
    bar.blockSignals(True)
    bar.setValue(0)
    bar.blockSignals(False)
    assert scroll.history is None
    window.controller.loop.provider = ScriptedProvider(
        [[TextDelta("新回合回答"), ModelTurn("新回合回答")]]
    )
    window.input.setPlainText("继续检查原理图")
    window.actions.submit()
    settle(window)
    assert bar.value() == bar.maximum()
    shown = view.toPlainText()
    assert "继续检查原理图" in shown and "新回合回答" in shown


def test_latest_returns_to_the_live_tail_without_a_frozen_history(window):
    """激活会话后即使滚动条停在旧位置，也必须能回到实时尾部。"""

    scroll, view = window.chat_scroll, window.display
    bar = view.verticalScrollBar()
    scroll.reset()
    bar.blockSignals(True)
    bar.setValue(0)
    bar.blockSignals(False)
    assert scroll.history is None
    assert bar.value() < bar.maximum()
    assert scroll.latest() is True
    assert bar.value() == bar.maximum()


def test_delayed_and_failed_pages_can_be_retired_and_retried(window, monkeypatch):
    scroll = window.chat_scroll
    pending = Future()
    pending.set_running_or_notify_cancel()
    original = window.api.chat_page
    monkeypatch.setattr(window.api, 'chat_page', lambda *_args, **_kw: pending)
    scroll.load('before')
    assert scroll.receipt.pending is not None
    scroll.latest()
    pending.set_result({'invalid': 'late result'})
    QApplication.instance().processEvents()
    assert scroll.history is None and not scroll.error
    failed = Future()
    failed.set_exception(ValueError('unavailable'))
    monkeypatch.setattr(window.api, 'chat_page', lambda *_args, **_kw: failed)
    scroll.load('before')
    wait_until(lambda: scroll.receipt.pending is None)
    assert '重试' in scroll.error
    monkeypatch.setattr(window.api, 'chat_page', original)
    page_at_edge(window, 'before')
    assert not scroll.error


def test_readonly_preview_uses_the_same_scroll_history(window):
    from agent_gui_wait import activate

    session_id = window.controller.session_id
    window.sessions.create(window.controller.current, session_id='other')
    activate(window, 'other')
    window.page.preview_session(session_id)
    settle(window)
    wait_until(lambda: not window.presentation.replay.dirty)
    page_at_edge(window, 'before')
    assert window.chat_scroll.history.read_only
    assert window.details.current_transcript() is window.chat_scroll.history
    window.page.return_from_preview()
    settle(window)
    assert window.chat_scroll.history is None
