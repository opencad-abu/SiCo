"""Published event ranges are durable, bounded and independent of Qt work."""

from sico.service.frontend_session import SessionToken

import threading
from dataclasses import replace

import pytest
from agent_gui_wait import settle, wait_until
from agent_replay_fixtures import blocked_append, history_item, record_task
from agent_worker_fixtures import heartbeat
from agent_worker_fixtures import window as window

from sico.service.event_display import BATCH_BYTES, BATCH_EVENTS, EVENT_BYTES, encoded
from sico.service.published import freeze
from sico.core.native_origin import NativeOrigins
from sico_ui.event_batches import BatchDelivery


def stream_for(window, messages=200):
    return window.sessions.worker.prepare_replay(
        SessionToken(window.controller.session_id, window.controller.runtime_id), window.page.activation, messages,
    ).result(5).stream


def next_batch(window, stream):
    future = window.sessions.worker.event_batch(stream)
    value = future.result(5)
    stream.received(future)
    return value


def test_slow_snapshot_keeps_qt_heartbeat_and_previous_state(window, monkeypatch):
    controller = window.controller
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()
    snapshot = controller.updates._state_snapshot
    before = window.presentation.state

    def slow(committed):
        assert threading.get_ident() != owner
        entered.set()
        assert release.wait(5)
        return snapshot(committed)

    monkeypatch.setattr(controller.updates, "_state_snapshot", slow)
    controller.version += 1
    window.binding.poll()
    try:
        wait_until(entered.is_set)
        window.binding.poll()
        heartbeat(window)
        assert window.presentation.state is before
        assert not release.is_set()
        # Enqueue/close do not acquire the lock held by snapshot generation.
        window.input.setPlainText("responsive during state generation")
        assert window.input.toPlainText().startswith("responsive")
    finally:
        release.set()
    settle(window)
    assert window.presentation.state["version"] == controller.version


@pytest.mark.parametrize("boundary", ["write", "fsync"])
def test_published_events_never_precede_commit(window, monkeypatch, boundary):
    stream = stream_for(window)
    first = next_batch(window, stream)
    before = first.end
    with blocked_append(monkeypatch, window.controller, boundary=boundary) as gate:
        future = window.sessions.worker.event_batch(stream)
        heartbeat(window)
        assert not future.done() and not gate.release.is_set()
        assert len(window.controller.journal.records) == before
    batch = future.result(5)
    stream.received(future)
    assert batch.start == before + 1 and batch.end == before + 1
    assert batch.committed == batch.end


def test_batches_keep_raw_ranges_and_snapshot_watermark_after_coalescing(window):
    controller = window.controller
    record_task(controller.journal, controller.current, "first", terminal=False)
    state = controller.journal.state
    for _ in range(150):
        controller.journal.append("model.delta", {"text": "part"}, state)
    controller.journal.append("model.completed", {"text": "complete answer"}, state)
    state.task["status"] = "completed"
    controller.journal.append("task.completed", {}, state)
    stream = stream_for(window)
    batch = next_batch(window, stream)
    assert not batch.error
    assert batch.start == 1 and batch.end == len(controller.journal.records)
    assert len(batch.events) < batch.end
    assert all(event["kind"] != "model.delta" for event in batch.events)
    assert batch.snapshot[0]["sequence"] == batch.end == batch.committed
    idle = next_batch(window, stream)
    assert idle.start == batch.end + 1 and idle.end == batch.end
    assert not idle.events and idle.snapshot is None


def test_wide_and_large_events_are_bounded_and_full_body_is_verified(window):
    controller, journal = window.controller, window.controller.journal
    _, state = record_task(journal, controller.current, "large", terminal=False)
    text = "长正文" * 100000
    journal.append("model.completed", {"text": text}, state)
    state.task["status"] = "completed"
    journal.append("task.completed", {}, state)
    stream = stream_for(window)
    seen = []
    while True:
        batch = next_batch(window, stream)
        assert not batch.error
        assert batch.size <= BATCH_BYTES and len(batch.events) <= BATCH_EVENTS
        assert all(len(encoded(event)) <= EVENT_BYTES for event in batch.events)
        seen.extend(batch.events)
        if batch.end == batch.committed:
            break
    event = next(row for row in seen if row["kind"] == "model.completed")
    assert len(event["payload"]["text"]) < len(text)
    page = window.sessions.worker.event_detail(stream, event["detail"], 4096).result(5)
    assert page["text"] == text[4096:8192] and page["total"] == len(text)
    with pytest.raises(ValueError, match="no longer matches"):
        stream.detail(event["detail"][:-1] + ("0" if event["detail"][-1] != "0" else "1"))
    with pytest.raises(TypeError):
        event["payload"]["text"] = "corrupted"


def test_idle_poll_never_copies_origins_and_invalid_batch_is_atomic(window, monkeypatch):
    journal, controller = window.controller.journal, window.controller
    journal.append("codex.thread", {"thread_id": "thread"}, controller.loop.state)
    record_task(journal, controller.current, "old", native=True)
    stream = stream_for(window)
    next_batch(window, stream)

    def forbidden(*_args):
        raise AssertionError("Cumulative origin copy is forbidden")

    monkeypatch.setattr(NativeOrigins, "copy", forbidden)
    for _ in range(3):
        assert not next_batch(window, stream).events
    journal.append("codex.history.item", history_item(journal), controller.loop.state)
    assert not next_batch(window, stream).error
    before = stream.cursor.sequence
    bad = history_item(journal)
    bad["origin"]["task_id"] = "wrong"
    journal.append("codex.history.item", bad, controller.loop.state)
    batch = next_batch(window, stream)
    assert batch.error and stream.cursor.sequence == before


@pytest.mark.parametrize("damage", ["duplicate", "gap", "runtime", "activation", "snapshot"])
def test_qt_rejects_invalid_publication_before_rendering(window, monkeypatch, damage):
    stream = stream_for(window)
    delivery = BatchDelivery(window.sessions.worker, stream)
    future = stream.request(window.sessions.events)
    batch = future.result(5)
    if damage == "duplicate":
        batch = replace(batch, start=batch.start - 1)
    elif damage == "gap":
        batch = replace(batch, start=batch.start + 1)
    elif damage == "runtime":
        batch = replace(batch, identity=(batch.identity[0], "old", *batch.identity[2:]))
    elif damage == "activation":
        batch = replace(batch, activation=batch.activation - 1)
    else:
        state, contexts = batch.snapshot
        batch = replace(batch, snapshot=(freeze({**state, "sequence": batch.end + 1}), contexts))
    monkeypatch.setattr("sico_ui.event_batches.completed_result", lambda _: batch)
    delivery.future = future
    rendered = []
    with pytest.raises(ValueError):
        delivery.apply(rendered.append, lambda *_: None)
    assert not rendered and delivery.cursor.sequence == 0


def test_qt_budget_leaves_remaining_items_for_next_tick(window, monkeypatch):
    controller = window.controller
    for number in range(40):
        controller.journal.append("session.note", {"text": str(number)}, controller.loop.state)
    stream = stream_for(window)
    delivery = BatchDelivery(window.sessions.worker, stream)
    delivery.future = stream.request(window.sessions.events)
    delivery.future.result(5)
    ticks = iter([0.0, 0.009])
    rendered = []
    with monkeypatch.context() as patch:
        patch.setattr("sico_ui.event_batches.time.monotonic", lambda: next(ticks))
        assert not delivery.apply(rendered.append, lambda *_: None)
    assert len(rendered) == 1 and delivery.batch is not None
    while not delivery.apply(rendered.append, lambda *_: None):
        pass
    assert [event["sequence"] for event in rendered] == list(range(1, 42))


def test_single_pending_publication_and_close_cancel_queued_work(window):
    entered, release = threading.Event(), threading.Event()

    def slow():
        entered.set()
        assert release.wait(5)

    stream = stream_for(window)
    blocker = window.sessions.events._request(slow)
    try:
        assert entered.wait(5)
        first = window.sessions.worker.event_batch(stream)
        assert all(window.sessions.worker.event_batch(stream) is first for _ in range(100))
        assert window.sessions.events._queue.qsize() == 1
        stream.close()
        assert first.cancelled()
        heartbeat(window)
    finally:
        release.set()
    blocker.result(5)


def test_published_contexts_and_snapshots_are_owned_by_backend(window):
    stream = stream_for(window)
    state, contexts = next_batch(window, stream).snapshot
    with pytest.raises(TypeError):
        state["requests"].append({})
    with pytest.raises(TypeError):
        contexts[0].snapshot["foreign"] = True
    original = state["context"]["snapshot"]
    window.controller.current.snapshot["later"] = True
    assert "later" not in original and "later" not in contexts[0].snapshot
