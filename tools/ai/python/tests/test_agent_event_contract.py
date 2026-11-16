"""Races, backpressure and replay continuity of the background display contract."""

import threading
from dataclasses import replace

import pytest
from agent_gui_wait import wait_until
from agent_replay_fixtures import record_task
from agent_worker_fixtures import heartbeat
from agent_worker_fixtures import window as window
from test_agent_event_publication import next_batch, stream_for

from sico.core.contracts import BoundContext
from sico.service.event_display import EventDisplay, coalesce
from sico.storage.history import SessionReader
from sico_ui.event_batches import BatchDelivery
from sico_ui.transcript import Transcript


@pytest.mark.parametrize("stage", ["ready", "partially_applied"])
@pytest.mark.parametrize("change", ["runtime", "generation", "reopened", "close"])
def test_ready_batches_cannot_cross_a_runtime_change(window, monkeypatch, stage, change):
    owner = window.controller
    for number in range(40):
        owner.journal.append("session.note", {"text": str(number)}, owner.loop.state)
    stream = stream_for(window)
    delivery = BatchDelivery(window.sessions.worker, stream)
    delivery.request()
    delivery.future.result(5)
    rendered = []
    if stage == "partially_applied":
        # Exhaust the time budget independently of the replay catch-up batch size.
        with monkeypatch.context() as patch:
            patch.setattr(delivery, "BUDGET", 0)
            assert not delivery.apply(rendered.append, lambda *_: None)
        assert len(rendered) == 1
    before = len(rendered)
    runtime, context = owner.runtime_id, owner.current
    if change == "runtime":
        owner.runtime_id = "new-runtime"
    elif change == "generation":
        owner.current = BoundContext(context.instance_id, "new", context.target_id, {})
    elif change == "reopened":
        window.sessions.controllers[owner.session_id] = object()
    else:
        owner.request_close()
    try:
        with pytest.raises(ValueError):
            delivery.apply(rendered.append, lambda *_: None)
        assert len(rendered) == before
    finally:
        owner.runtime_id, owner.current = runtime, context
        window.sessions.controllers[owner.session_id] = owner
        delivery.close()


@pytest.mark.parametrize("damage", ["duplicate", "gap"])
def test_raw_sequence_errors_fail_before_projection(window, monkeypatch, damage):
    owner = window.controller
    record_task(owner.journal, owner.current, "task")
    stream = stream_for(window)
    original = owner.journal.committed_batch
    projected = []

    def damaged(*args):
        rows, committed = original(*args)
        if damage == "duplicate":
            rows.insert(1, rows[0])
        else:
            del rows[0]
        return rows, committed

    monkeypatch.setattr(owner.journal, "committed_batch", damaged)
    monkeypatch.setattr(stream.display, "project", projected.append)
    assert next_batch(window, stream).error
    assert not projected and stream.cursor.sequence == 0


def test_reply_crossing_raw_batches_and_tool_rounds_is_complete(window):
    owner = window.controller
    _, state = record_task(owner.journal, owner.current, "task", terminal=False)
    for _ in range(530):
        owner.journal.append("model.delta", {"text": "a"}, state)
    owner.journal.append("model.completed", {"text": "a" * 530 + " final"}, state)
    owner.journal.append("tool.started", {"id": "call", "name": "lookup"}, state)
    owner.journal.append("tool.finished", {"id": "call", "name": "lookup",
                                          "result": {"status": "ok"}}, state)
    owner.journal.append("model.delta", {"text": "next"}, state)
    owner.journal.append("model.completed", {"text": "next answer"}, state)
    state.task["status"] = "completed"
    owner.journal.append("task.completed", {}, state)
    stream, transcript = stream_for(window), Transcript()
    ends = []
    while True:
        batch = next_batch(window, stream)
        assert not batch.error
        ends.append(batch.end)
        for event in batch.events:
            transcript.receive(event)
        if batch.end == batch.committed:
            break
    assert len(ends) > 2
    assert [m["text"] for m in transcript.messages if m["role"] == "assistant"] == [
        "a" * 530 + " final", "next answer",
    ]
    # A completion after a tool coalesces only its own next model segment.
    rows = owner.journal.events()
    start = next(i for i, e in enumerate(rows) if e["kind"] == "tool.started")
    fragment = rows[start - 1:]
    kept = coalesce(fragment)
    assert not any(e["kind"] == "model.delta" for e in kept)
    assert any(e["kind"] == "model.completed" and e["payload"].get("text") == "next answer"
               for e in kept)
    assert coalesce([rows[start - 2], rows[start]])[0]["kind"] == "model.delta"


def test_idle_qt_never_serializes_copies_or_rebuilds_state(window, monkeypatch):
    import sico.core.contracts as contracts
    import sico.service.session_updates as updates

    owner_thread = threading.get_ident()

    def checked(original):
        def run(*args, **kwargs):
            assert threading.get_ident() != owner_thread
            return original(*args, **kwargs)
        return run

    monkeypatch.setattr(contracts, "json_copy", checked(contracts.json_copy))
    monkeypatch.setattr(updates, "deepcopy", checked(updates.deepcopy))
    before = window.presentation.state
    for _ in range(3):
        window.binding.poll()
        heartbeat(window)
    assert window.presentation.state is before
    version = window.controller.version
    window.controller.interrupt()  # Does not increment the journal/state version.
    wait_until(lambda: window.presentation.state["paused"])
    assert window.controller.version == version
    assert window.presentation.state["paused"]


def test_preview_streams_a_fixed_committed_prefix_without_full_copy(window, monkeypatch):
    owner = window.sessions.worker.open_session("history").result(5)
    for number in range(180):
        record_task(owner.journal, owner.current, "t" + str(number))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("A preview must not materialize the full history")

    monkeypatch.setattr(owner.journal, "events", forbidden)
    monkeypatch.setattr(SessionReader, "events", forbidden)
    prepared = window.sessions.worker.preview_session("history", 1).result(5)
    record_task(owner.journal, owner.current, "after-preparation")
    rows = list(prepared.stream.iter_events())
    assert rows[-1]["sequence"] == prepared.committed_sequence
    assert all(e.get("task_id") != "after-preparation" for e in rows)
    assert not isinstance(prepared.stream.events, (list, tuple))


def test_changed_passive_file_requires_a_new_preview(window):
    prepared = window.sessions.worker.preview_session("history", 1).result(5)
    owner = window.sessions.worker.open_session("history").result(5)
    record_task(owner.journal, owner.current, "later")
    assert next_batch(window, prepared.stream).error


def test_event_queue_backpressure_cancellation_and_close_are_bounded(window):
    entered, release = threading.Event(), threading.Event()
    stream = stream_for(window)

    def hold():
        entered.set()
        assert release.wait(5)

    blocker = window.sessions.events._request(hold)
    try:
        assert entered.wait(5)
        queued = [window.sessions.events._request(lambda: None) for _ in range(64)]
        with pytest.raises(ValueError, match="queue is full"):
            window.sessions.worker.event_batch(stream)
        assert stream._future is None
        window.sessions.events.close()
        assert all(f.cancelled() for f in queued)
        stream.close()
        heartbeat(window)
        assert not release.is_set()
    finally:
        release.set()
    blocker.result(5)


def test_audit_projection_preserves_ids_and_answer_keys(window):
    q = {"id": "question", "header": "标题", "question": "问题" * 1000,
         "options": [{"label": "option", "description": "描述" * 500}]}
    raw = {"sequence": 8, "session_id": "initial", "task_id": "task",
           "kind": "workbench.audit.prepared", "payload": {"id": "audit", "title": "title",
           "evidence": [{"large": "x" * 4000}] * 400, "questions": [q]}}
    event, _ = EventDisplay().project(raw)
    assert event["detail"] and event["payload"]["questions"][0]["id"] == "question"
    assert event["payload"]["questions"][0]["options"][0]["label"] == "option"
    reply = {**raw, "kind": "workbench.audit.reply_received",
             "payload": {"id": "audit", "answers": {"question": {"choice": "option"}}}}
    assert EventDisplay().project(reply)[0]["payload"]["answers"] == reply["payload"]["answers"]


def test_typed_audit_projection_keeps_form_schema_or_fails_explicitly():
    schema = {"type": "object", "properties": {
        "choice": {"type": "string", "title": "Choice", "enum": ["yes", "no"]},
    }, "required": ["choice"]}
    raw = {"sequence": 9, "session_id": "initial", "task_id": "task",
           "kind": "workbench.audit.prepared", "payload": {
               "id": "audit", "title": "typed", "elicitation": {
                   "mode": "form", "message": "choose", "schema": schema,
               },
           }}
    event, _ = EventDisplay().project(raw)
    assert event["payload"]["elicitation"]["schema"] == schema
    raw["payload"]["elicitation"]["schema"]["properties"]["choice"]["description"] = "x" * 40000
    with pytest.raises(ValueError, match="Typed audit interaction"):
        EventDisplay().project(raw)


def test_revert_projection_keeps_the_boundary_identity_when_clipped():
    raw = {
        "sequence": 10, "session_id": "initial", "task_id": "task",
        "kind": "codex.history.reverted",
        "payload": {
            "thread_id": "thread", "before_turn_id": "turn-42",
            "retained_turn_ids": [f"retained-{index}" for index in range(1000)],
            "removed_turn_ids": [f"removed-{index}" for index in range(1000)],
        },
    }
    event, _ = EventDisplay().project(raw)
    assert event["detail"]
    assert event["payload"]["thread_id"] == "thread"
    assert event["payload"]["before_turn_id"] == "turn-42"


def test_old_workbench_publication_keeps_owned_nested_rows(window):
    from agent_window_fixture import workbench_backend

    source = workbench_backend(window)
    _, original = source.snapshot(-1).result(5)
    changed = replace(original, data={"d": {"nested": ["old"]}})
    with pytest.raises(TypeError):
        changed.data["d"]["nested"].append("new")
    record_task(window.controller.journal, window.controller.current, "later")
    _, latest = source.snapshot(-1).result(5)
    assert latest.version > original.version and not original.records
    assert changed.data["d"]["nested"] == ["old"]


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_detail_close_or_page_change_discards_late_result(window, monkeypatch, outcome):
    from concurrent.futures import Future

    from PyQt5.QtWidgets import QTextBrowser

    from sico_ui.event_detail import show_event_detail
    from sico_ui.receipts import DataReceipt

    future = Future()
    future.set_running_or_notify_cancel()
    monkeypatch.setattr(window.sessions.worker, "event_detail", lambda *args: future)
    show_event_detail(window, "placeholder")
    receipt = next(r for r in window.findChildren(DataReceipt) if r.pending
                   and r.pending[0] is future)
    dialog = receipt.parent()
    view = dialog.findChild(QTextBrowser)
    window.page.preview_session("history")
    if outcome == "failure":
        future.set_exception(ValueError("late failure"))
    else:
        future.set_result({"text": "late success", "offset": 0, "next": 12, "total": 12})
    receipt.poll()
    assert not dialog.isVisible()
    assert "late" not in view.toPlainText()
    wait_until(lambda: not window.page.opening)


@pytest.mark.parametrize("kind", ["event", "input"])
def test_history_detail_reads_preview_stream_without_activating_session(window, monkeypatch, kind):
    from concurrent.futures import Future

    from PyQt5.QtWidgets import QPlainTextEdit, QTextBrowser

    from sico_ui.event_detail import show_event_detail
    from sico_ui.receipts import DataReceipt
    from sico_ui.turn_input_detail import show_input_detail

    live = window.page.session
    window.page.preview_session("history")
    wait_until(lambda: not window.page.opening)
    expected = window.page_presentation.delivery.stream
    observed = []
    future = Future()

    def read(feed, *args):
        observed.append(feed)
        return future

    monkeypatch.setattr(window.api, kind + "_detail", read)
    if kind == "event":
        show_event_detail(window, "history-record")
        result = {"text": "archived detail", "offset": 0, "next": 15, "total": 15}
    else:
        show_input_detail(window, "1")
        result = {"text": "archived detail", "inputs": []}
    assert observed == [expected]
    assert expected is not window.binding.delivery.stream
    receipt = next(r for r in window.findChildren(DataReceipt)
                   if r.pending and r.pending[0] is future)
    dialog = receipt.parent()
    try:
        future.set_result(result)
        receipt.poll()
        view = dialog.findChild(QTextBrowser if kind == "event" else QPlainTextEdit)
        assert view.toPlainText() == "archived detail" and view.isReadOnly()
        assert window.page.session == live and window.page.reviewing == "history"
        assert not window.send.isEnabled()
    finally:
        dialog.close()
