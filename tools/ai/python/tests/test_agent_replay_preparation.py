"""Prepared replay boundaries remain durable, continuous and source validated."""

from copy import deepcopy

import pytest
from agent_replay_fixtures import background, blocked_append, history_item, record_task

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.service.controller import SessionController
from sico.service.replay import connection, prepare_replay
from sico.storage.journal import SessionJournal


@pytest.fixture
def controller(tmp_path):
    context = BoundContext("instance", "generation", "target", {})
    with SessionJournal(tmp_path, "replay") as journal:
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, context)
        journal.append("codex.thread", {"thread_id": "thread"}, loop.state)
        record_task(journal, context, "old", native=True)
        record_task(journal, context, "current", native=True)
        owner = SessionController(loop)
        try:
            yield owner
        finally:
            owner.close()
            assert owner.wait(5)


@pytest.mark.parametrize("boundary", ["write", "fsync"])
def test_preparation_waits_for_commit_and_publishes_one_consistent_watermark(
    controller, monkeypatch, boundary,
):
    journal = controller.journal
    before = len(journal.records)
    with blocked_append(monkeypatch, controller, boundary=boundary):
        future, thread = background(lambda: prepare_replay(
            controller, connection(controller), 7, 1,
        ))
        assert not future.done()
        assert len(journal.records) == before
    prepared = future.result(5)
    thread.join(5)
    assert prepared.committed_sequence == before + 1
    assert prepared.activation == 7 and prepared.matches(controller, 7)
    assert not prepared.matches(controller, 8)
    events = prepared.cursor.consume(controller.updates.read_updates(prepared.cursor.sequence))
    assert events[-1]["sequence"] == before + 1
    assert prepared.cursor.sequence == prepared.committed_sequence


def test_tail_origins_and_post_preparation_events_join_once(controller):
    journal = controller.journal
    prepared = prepare_replay(controller, connection(controller), 3, 1)
    start = prepared.cursor.sequence + 1
    assert start > 1
    assert set(prepared.cursor.native_origins.tasks) == {"old"}
    watermark = prepared.committed_sequence
    late = history_item(journal)
    journal.append("codex.history.item", late, controller.loop.state)
    record_task(journal, controller.current, "new", native=True)
    journal.append("codex.history.item", history_item(journal, "new"), controller.loop.state)
    seen = []
    while True:
        events = prepared.cursor.consume(controller.updates.read_updates(prepared.cursor.sequence, limit=2))
        seen.extend(events)
        if not events:
            break
    assert [e["sequence"] for e in seen] == list(range(start, len(journal.records) + 1))
    assert seen[-1]["sequence"] > watermark
    origins = [e["payload"]["origin"]["task_id"] for e in seen if e["kind"] == "codex.history.item"]
    assert origins == ["old", "new"]


@pytest.mark.parametrize("damage", ["duplicate", "gap", "origin", "runtime", "generation"])
def test_prepared_cursor_keeps_identity_order_and_atomic_rejection(controller, damage):
    journal = controller.journal
    journal.append("codex.history.item", history_item(journal), controller.loop.state)
    prepared = prepare_replay(controller, connection(controller), 1, 1)
    cursor = prepared.cursor
    batch = controller.updates.read_updates(cursor.sequence)
    bad = deepcopy(batch)
    if damage == "duplicate":
        bad["events"].insert(1, bad["events"][0])
    elif damage == "gap":
        del bad["events"][0]
    elif damage == "origin":
        bad["events"][-1]["payload"]["origin"]["task_id"] = "current"
    else:
        bad["runtime_id" if damage == "runtime" else "generation"] = "foreign"
    before = cursor.sequence
    with pytest.raises(ValueError):
        cursor.consume(bad)
    assert cursor.sequence == before and cursor.task_id == ""
    cursor.consume(batch)
    assert cursor.sequence == prepared.committed_sequence


def test_reactivation_does_not_reclassify_foreign_live_events_as_history(controller):
    foreign = BoundContext("other", "connection", "target", {})
    record_task(controller.journal, foreign, "foreign")
    prepared = prepare_replay(controller, connection(controller), 1, 1)
    assert prepared.committed_sequence > prepared.cursor.history_sequence
    with pytest.raises(ValueError, match="another Virtuoso"):
        prepared.cursor.consume(controller.updates.read_updates(prepared.cursor.sequence))


def test_preparation_failure_does_not_publish_partial_result(controller, monkeypatch):
    def broken(before):
        raise OSError("origin preparation failed")

    monkeypatch.setattr(controller.journal, "native_origins", broken)
    future, thread = background(lambda: prepare_replay(controller, connection(controller), 1, 1))
    with pytest.raises(OSError, match="origin preparation failed"):
        future.result(5)
    thread.join(5)
