"""Deterministic event-loop scheduling, renderer ownership and worker cleanup."""

from sico.service.frontend_session import SessionToken

import threading
from types import SimpleNamespace

import pytest
from agent_gui_wait import settle, wait_until
from agent_worker_fixtures import heartbeat
from agent_worker_fixtures import window as window
from PyQt5.QtWidgets import QTextBrowser

from sico_ui.tasks import TaskPanel
from sico_ui.transcript import Transcript


def test_renderer_checks_time_after_each_bubble_and_resumes(window, monkeypatch):
    transcript, view = Transcript(), QTextBrowser(window)
    for number in range(20):
        transcript.append("assistant", "bubble " + str(number))
    calls, elapsed = [], [0.0]
    insert = transcript._insert

    def measured(*args, **kwargs):
        calls.append(True)
        result = insert(*args, **kwargs)
        elapsed[0] += transcript.RENDER_BUDGET * 2
        return result

    monkeypatch.setattr(transcript, "_insert", measured)
    with monkeypatch.context() as patch:
        patch.setattr("sico_ui.transcript_render.time",
                      SimpleNamespace(monotonic=lambda: elapsed[0]))
        transcript.render(view)
    assert len(calls) == 1 and transcript.dirty
    assert "bubble 0" in view.toPlainText() and "bubble 19" not in view.toPlainText()
    wait_until(lambda: not transcript.dirty)
    assert len(calls) == 20 and "bubble 19" in view.toPlainText()


def test_switching_renderer_cancels_old_document_continuation(window):
    old, new, view = Transcript(), Transcript(), QTextBrowser(window)
    for _ in range(100):
        old.append("assistant", "old page")
    old.render(view)
    assert old.dirty
    new.append("assistant", "new page")
    new.render(view, force=True)
    heartbeat(window)
    assert "new page" in view.toPlainText() and "old page" not in view.toPlainText()
    assert not old._render_timer.isActive()


def test_task_row_continuation_is_replaced_by_new_snapshot(window):
    panel = TaskPanel()
    context = window.controller.current.record()
    rows = [{"id": str(i), "text": "old", "status": "queued", "context": context}
            for i in range(200)]
    panel.update_requests(rows)
    assert panel.topLevelItemCount() <= 16 and panel._paint_rows_pending
    panel.update_requests([{**rows[0], "id": "latest", "text": "latest"}])
    heartbeat(window)
    assert panel.topLevelItemCount() == 1
    assert set(panel.items) == {"latest"}


@pytest.mark.parametrize("action", ["switch", "close"])
def test_slow_snapshot_does_not_block_page_or_close(window, monkeypatch, action):
    entered, release = threading.Event(), threading.Event()
    original = window.controller.updates._state_snapshot

    def slow(sequence):
        entered.set()
        assert release.wait(5)
        return original(sequence)

    monkeypatch.setattr(window.controller.updates, "_state_snapshot", slow)
    window.controller.version += 1
    window.binding.poll()
    wait_until(entered.is_set)
    try:
        if action == "switch":
            window.page.preview_session("history")
            assert window.page.reviewing == "history" and window.page.opening
        else:
            window.request_quit()
            assert window.lifecycle.closing
        heartbeat(window)
        assert not release.is_set()
    finally:
        release.set()
    if action == "switch":
        wait_until(lambda: not window.page.opening)
        window.page.return_from_preview()
        settle(window)


def test_abandoned_preparation_and_closed_stream_retire_only_on_worker(window, monkeypatch):
    from sico.service.event_stream import EventStream

    entered, release, retired = threading.Event(), threading.Event(), []
    original = window.controller.journal.prepare_replay
    retire = EventStream.retire
    owner = threading.get_ident()

    def slow(messages, *, after=None):
        entered.set()
        assert release.wait(5)
        return original(messages, after=after)

    def cleanup(stream):
        assert threading.get_ident() != owner
        retired.append(stream)
        return retire(stream)

    monkeypatch.setattr(window.controller.journal, "prepare_replay", slow)
    monkeypatch.setattr(EventStream, "retire", cleanup)
    pending = window.sessions.worker.prepare_replay(SessionToken(window.controller.session_id, window.controller.runtime_id), 30, 200)
    try:
        assert entered.wait(5)
        pending.discard()
        assert not pending.cancel()
        heartbeat(window)
    finally:
        release.set()
    prepared = pending.result(5)
    wait_until(lambda: prepared.stream in retired)
    assert prepared.cursor is None
    assert prepared.stream not in window.sessions.events._streams
    assert not prepared.stream._pending


def test_hidden_completion_can_precede_event_delivery_and_paint(window, monkeypatch):
    from sico.core.contracts import ModelTurn, TextDelta
    from sico.providers.mock import ScriptedProvider
    from sico.service.event_stream import EventStream

    entered, release = threading.Event(), threading.Event()
    original = EventStream._next
    controller = window.controller
    controller.loop.provider = ScriptedProvider([
        [TextDelta("before after"), ModelTurn("before after")]])

    def delayed(stream, start):
        if stream.controller is controller:
            entered.set()
            assert release.wait(5)
        return original(stream, start)

    monkeypatch.setattr(EventStream, "_next", delayed)
    try:
        window.restore()
        window.input.setPlainText("continue while hidden")
        window.actions.submit()
        window.hide()
        wait_until(entered.is_set)
        wait_until(lambda: controller.loop.state.task.get("status") == "completed")
        window.restore()
        assert window.isVisible()
        assert "before after" not in window.display.toPlainText()
    finally:
        release.set()
    settle(window)
    assert "before after" in window.display.toPlainText()
