"""History paging crosses the real frontend socket and keeps subscription scope."""

import threading

import pytest
from agent_gui_wait import wait_until
from agent_replay_fixtures import record_task
from agent_service_gui_fixtures import remote_window
from agent_worker_fixtures import heartbeat
from sico.service.chat_archive import ChatArchive


def test_remote_paging_is_async_and_retired_subscription_cannot_leak(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    with remote_window(tmp_path, monkeypatch) as (service, window):
        controller = service.owner.controllers['live']
        _, state = record_task(controller.journal, controller.current, 'large', terminal=False)
        for index in range(450):
            controller.journal.append('model.completed', {'text': f'remote answer {index}'}, state)
        state.task['status'] = 'completed'
        controller.journal.append('task.completed', {}, state)
        end = len(controller.journal.records)
        wait_until(lambda: window.binding.delivery.cursor.sequence >= end)
        wait_until(lambda: not window.presentation.transcript.dirty)
        original = ChatArchive.sync

        def held(archive, watermark):
            entered.set()
            assert release.wait(5)
            return original(archive, watermark)

        monkeypatch.setattr(ChatArchive, 'sync', held)
        try:
            window.chat_scroll.load('before')
            wait_until(entered.is_set)
            heartbeat(window)
            assert window.chat_scroll.receipt.pending is not None
            assert not release.is_set()
            release.set()
            wait_until(lambda: window.chat_scroll.receipt.pending is None)
            assert not window.chat_scroll.error
            for _ in range(10):
                if not window.chat_scroll.older:
                    break
                window.display.verticalScrollBar().setValue(0)
                window.chat_scroll.load('before')
                wait_until(lambda: window.chat_scroll.receipt.pending is None)
                assert not window.chat_scroll.error
            assert not window.chat_scroll.older
            assert 'remote answer 0' in window.display.toPlainText()
            feed = window.binding.delivery.stream
            feed.close()
            with pytest.raises(ValueError):
                window.api.chat_page(feed, end=end)
        finally:
            release.set()
