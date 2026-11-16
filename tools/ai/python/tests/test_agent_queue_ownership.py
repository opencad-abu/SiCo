"""Queue shutdown callbacks and publication ownership cannot hold frontend locks."""

import threading
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from agent_worker_fixtures import heartbeat
from agent_worker_fixtures import window as window

from sico.service.event_stream import EventStream
from sico.service.published import freeze, thaw


@pytest.mark.parametrize("queue_name", ["worker", "data", "events", "targets"])
def test_full_queue_and_reentrant_cancel_remain_responsive(window, queue_name):
    service = getattr(window.sessions, queue_name)
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    def hold():
        entered.set()
        assert release.wait(5)

    running = service._request(hold)
    assert entered.wait(2)
    pending = []
    observed = []
    try:
        for _ in range(64):
            future = service._request(lambda: None)
            future.add_done_callback(lambda _done: observed.append(service.busy))
            pending.append(future)
        with pytest.raises(ValueError, match="full"):
            service._request(lambda: None)
        heartbeat(window)
        closer = threading.Thread(target=lambda: (service.close(), closed.set()), daemon=True)
        closer.start()
        assert closed.wait(2), "cancel callback deadlocked while reading queue state"
        assert len(observed) == 64 and all(f.cancelled() for f in pending)
        heartbeat(window)
        assert not release.is_set()
    finally:
        release.set()
    running.result(5)
    assert service.wait(5)


def test_event_cancel_callback_can_reenter_stream_close():
    stream = EventStream(None, SimpleNamespace(identity=("s", "r"), sequence=0), 1, 0)
    future = Future()
    stream._future = future
    future.add_done_callback(lambda _done: stream.close())
    done = threading.Event()
    thread = threading.Thread(target=lambda: (stream.close(), done.set()), daemon=True)
    thread.start()
    assert done.wait(2), "stream cancellation ran under its publication lock"
    assert future.cancelled() and stream._future is None


def test_nested_publication_owns_data_and_consumer_edits_are_detached():
    original = {"rows": [{"values": [1, 2]}]}
    published = freeze(original)
    original["rows"][0]["values"].append(3)
    assert published["rows"][0]["values"] == [1, 2]
    with pytest.raises(TypeError):
        published["rows"][0]["values"].append(9)
    editable = thaw(published)
    editable["rows"].clear()
    assert len(published["rows"]) == 1


def test_slow_catalog_freeze_never_holds_snapshot_lock(window, monkeypatch):
    import sico.service.catalog as catalog_module

    entered, release = threading.Event(), threading.Event()
    original = catalog_module.freeze

    def slow(value):
        assert threading.get_ident() != owner
        entered.set()
        assert release.wait(5)
        return original(value)

    owner = threading.get_ident()
    catalog = window.sessions.catalog
    previous = catalog.snapshot()
    monkeypatch.setattr(catalog_module, "freeze", slow)
    monkeypatch.setattr(catalog._index, "scan", lambda _progress: {
        "extra": {"id": "extra", "modified": 1, "title": "new"},
    })
    receipt = catalog.request()
    try:
        assert entered.wait(2)
        # Test lock availability first so a regression fails instead of hanging Qt.
        assert catalog._lock.acquire(timeout=0.1)
        catalog._lock.release()
        assert catalog.snapshot() is previous
        heartbeat(window)
        assert not release.is_set()
    finally:
        release.set()
    receipt.result(5)
