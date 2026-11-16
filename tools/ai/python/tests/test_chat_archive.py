"""History pages retain every message even inside one long task."""

from types import SimpleNamespace

import pytest
from agent_replay_fixtures import record_task
from sico.core.contracts import BoundContext, RunState
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.service.chat_archive import PAGE_BYTES, ChatArchive
from sico.service.chat_projection import ChatProjection
from sico.service.controller import SessionController
from sico.service.event_display import EventDisplay, coalesce, encoded
from sico.service.replay import connection, prepare_preview, prepare_replay
from sico.storage.journal import SessionJournal


@pytest.fixture
def history(tmp_path):
    context = BoundContext('instance', 'generation', 'target', {})
    with SessionJournal(tmp_path, 'chat-pages') as journal:
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, context)
        _, state = record_task(journal, context, 'one-long-task', terminal=False)
        for index in range(625):
            journal.append('model.completed', {'text': f'answer {index:04d}'}, state)
        state.task['status'] = 'completed'
        journal.append('task.completed', {}, state)
        owner = SessionController(loop)
        try:
            yield owner
        finally:
            owner.close()
            assert owner.wait(5)


def scan(archive, end):
    backward, before = [], 0
    while True:
        page = archive.page(before, 0, end)
        assert len(page['rows']) <= 60
        assert len(encoded(page['rows'])) <= PAGE_BYTES
        backward = list(page['rows']) + backward
        if not page['older']:
            break
        before = page['rows'][0]['message_id']
    forward, after = [], backward[0]['message_id']
    while True:
        page = archive.page(0, after, end)
        forward.extend(page['rows'])
        if not page['newer']:
            break
        after = page['rows'][-1]['message_id']
    assert backward[1:] == forward
    assert len({row['message_id'] for row in backward}) == len(backward)
    return backward


@pytest.mark.parametrize('preview', [False, True])
def test_bidirectional_pages_within_one_task(history, preview):
    if preview:
        sessions = SimpleNamespace(root=history.journal.root,
                                   controllers={history.session_id: history},
                                   events=SimpleNamespace(own=lambda stream: None))
        stream = prepare_preview(sessions, history.session_id, history.runtime_id, 1, 10).stream
    else:
        stream = prepare_replay(history, connection(history), 1, 10).stream
    archive = ChatArchive(stream)
    try:
        rows = scan(archive, stream.committed)
        assert [row['text'] for row in rows if row['role'] == 'assistant'] == [
            f'answer {i:04d}' for i in range(625)]
        assert rows[0]['text'] == 'one-long-task'
        assert len(archive.projection.messages) <= ChatProjection.RETAIN_MESSAGES
    finally:
        archive.close()
        stream.close()


def test_closed_file_history(history):
    root, session_id = history.journal.root, history.session_id
    history.close()
    assert history.wait(5)
    stream = prepare_preview(SimpleNamespace(root=root, controllers={},
                             events=SimpleNamespace(own=lambda stream: None)),
                             session_id, None, 1, 10).stream
    archive = ChatArchive(stream)
    try:
        rows = scan(archive, stream.committed)
        assert rows[0]['text'] == 'one-long-task'
        assert any(row['text'] == 'answer 0000' for row in rows)
    finally:
        archive.close()
        stream.close()


def test_frozen_watermark_excludes_new_tasks_then_can_advance(history):
    stream = prepare_replay(history, connection(history), 1, 10).stream
    archive = ChatArchive(stream)
    try:
        end = stream.committed
        before = archive.page(0, 0, end)
        record_task(history.journal, history.current, 'new-task')
        stream.committed = len(history.journal.records)
        assert archive.page(0, 0, end) == before
        after = archive.page(0, before['rows'][-1]['message_id'], stream.committed)
        assert 'new-task' in [row['text'] for row in after['rows']]
        with pytest.raises(ValueError, match='watermark'):
            archive.page(0, 0, end)
        stream.close()
        with pytest.raises(ValueError, match='closed'):
            archive.page(0, 0, stream.committed)
    finally:
        archive.close()


def test_live_and_archive_ids_agree_when_deltas_are_coalesced(history):
    _, state = record_task(history.journal, history.current, 'streamed', terminal=False)
    for text in ('start ', 'middle ', 'end'):
        history.journal.append('model.delta', {'text': text}, state)
    history.journal.append('model.completed', {'text': 'start middle end'}, state)
    state.task['status'] = 'completed'
    history.journal.append('task.completed', {}, state)
    stream = prepare_replay(history, connection(history), 1, 10).stream
    archive = ChatArchive(stream)
    try:
        page = archive.page(0, 0, stream.committed)
        live, display = ChatProjection(), EventDisplay()
        events = history.journal.events(0, limit=1000)
        for event in coalesce(events):
            projected = display.project(event)
            if projected:
                live.receive(projected[0])
        archived = next(row for row in page['rows'] if row['text'] == 'start middle end')
        shown = next(row for row in live.messages if row['text'] == 'start middle end')
        assert archived['message_id'] == shown['message_id']
        assert len([row for row in page['rows'] if row['text'] == 'start middle end']) == 1
    finally:
        archive.close()
        stream.close()


def test_archive_deletes_every_row_from_the_verified_revert_boundary(history):
    journal, context = history.journal, history.current
    record_task(journal, context, "before", native=True)
    record_task(journal, context, "boundary", native=True)
    record_task(journal, context, "later", native=True)
    state = RunState(context, task={"id": "rollback", "status": "executing",
                                   "pending_calls": []})
    journal.append("task.started", {
        "text": "Thread operation: rollback_to", "context": context.record(),
        "origin": "thread_operation",
    }, state)
    journal.append("codex.history.reverted", {
        "thread_id": "thread", "before_turn_id": "boundary",
        "retained_turn_ids": ["before"], "removed_turn_ids": ["boundary", "later"],
    }, state)
    state.task["status"] = "completed"
    journal.append("task.completed", {}, state)

    stream = prepare_replay(history, connection(history), 1, 10).stream
    archive = ChatArchive(stream)
    try:
        rows = scan(archive, stream.committed)
        assert [row["text"] for row in rows if row["role"] == "user"] == [
            "one-long-task", "before"]
        assert all("boundary" not in row["text"] and "later" not in row["text"]
                   for row in rows)
    finally:
        archive.close()
        stream.close()


def test_archive_revert_reaches_a_boundary_older_than_the_projection_tail(history):
    journal, context = history.journal, history.current
    _, state = record_task(journal, context, "old", terminal=False, native=True)
    for index in range(625):
        journal.append("model.completed", {"text": f"evicted {index:04d}"}, state)
    state.task["status"] = "completed"
    journal.append("task.completed", {}, state)
    operation = RunState(context, task={"id": "rollback", "status": "executing",
                                       "pending_calls": []})
    journal.append("task.started", {
        "text": "Thread operation: rollback_to", "context": context.record(),
        "origin": "thread_operation",
    }, operation)
    journal.append("codex.history.reverted", {
        "thread_id": "thread", "before_turn_id": "old",
        "retained_turn_ids": [], "removed_turn_ids": ["old"],
    }, operation)
    operation.task["status"] = "completed"
    journal.append("task.completed", {}, operation)

    stream = prepare_replay(history, connection(history), 1, 10).stream
    archive = ChatArchive(stream)
    try:
        rows = scan(archive, stream.committed)
        assert any(row["text"] == "one-long-task" for row in rows)
        assert all(row["text"] != "old" and not row["text"].startswith("evicted ")
                   for row in rows)
    finally:
        archive.close()
        stream.close()


@pytest.mark.parametrize('before,after,end', [(1, 2, 3), (-1, 0, 3), (True, 0, 3),
                                             (0, 0, 10**9)])
def test_invalid_page_bounds(history, before, after, end):
    stream = prepare_replay(history, connection(history), 1, 10).stream
    archive = ChatArchive(stream)
    try:
        with pytest.raises(ValueError):
            archive.page(before, after, end)
    finally:
        archive.close()
        stream.close()


def test_temporary_index_is_private_and_removed(history):
    import stat
    from pathlib import Path

    stream = prepare_replay(history, connection(history), 1, 10).stream
    archive = ChatArchive(stream)
    directory = Path(archive.directory.name)
    try:
        archive.page(0, 0, stream.committed)
        path = Path(archive.db.execute('PRAGMA database_list').fetchone()[2])
        assert path.parent == directory
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        archive.close()
        stream.close()
    assert not directory.exists()


def test_clipped_stream_and_startup_context_use_ordered_stable_ids():
    from sico.service.published import freeze

    def event(sequence, kind, payload):
        return freeze(dict(sequence=sequence, kind=kind, payload=payload,
                           session_id='s', task_id='t'))

    live, archived = ChatProjection(), ChatProjection()
    live.receive(event(1, 'task.started', {'origin': 'startup'}))
    archived.receive(event(1, 'task.started', {'origin': 'startup'}))
    live.receive(event(2, 'model.status', {'phase': 'thinking'}))
    result = event(3, 'tool.finished', {'name': 'get_context', 'result': {'status': 'ok'}})
    for projection in (live, archived):
        projection.receive(result)
    assert live.messages == archived.messages
    display = EventDisplay()
    for raw in (event(4, 'model.delta', {'text': 'x' * 20000}),
                event(5, 'model.completed', {'text': 'answer'})):
        projected, _ = display.project(raw)
        live.receive(projected)
    keys = [row['message_id'] for row in live.messages]
    assert keys == sorted(set(keys))
    assert sum(row['role'] == 'assistant' for row in live.messages) == 1


def test_archive_skips_display_noise_through_the_index(history, monkeypatch):
    """归档只投影消息相关行：被丢弃的 model.status/router.status 从不解析。"""

    journal = history.journal
    context = BoundContext('instance', 'generation', 'target', {})
    _, state = record_task(journal, context, 'partial-task', terminal=False)
    for index in range(30):
        journal.append('model.delta', {'text': f'partial {index:02d} '}, state)
    noise = []
    for index in range(200):
        noise.append(journal.append(
            'router.status', {'request_id': '%032d' % index, 'state': 'queued'}, state)['sequence'])
        noise.append(journal.append(
            'model.status', {'phase': 'waiting_model', 'label': '等待', 'tool': '',
                             'turn': 0, 'started_at': 'x'}, state)['sequence'])
    parsed = []
    original = SessionJournal.row

    def spy(self, position):
        parsed.append(position)
        return original(self, position)

    monkeypatch.setattr(SessionJournal, 'row', spy)
    stream = prepare_replay(history, connection(history), 1, 10).stream
    archive = ChatArchive(stream)
    try:
        rows = scan(archive, stream.committed)
        answers = [row['text'] for row in rows if row['role'] == 'assistant']
        assert [text for text in answers if text.startswith('answer')] == [
            f'answer {i:04d}' for i in range(625)]
        # 中断答案的 delta 文本必须保留（不能被行类预筛吞掉）。
        assert any('partial 00' in (text or '') for text in answers)
        assert parsed and set(parsed).isdisjoint({sequence - 1 for sequence in noise})
        assert archive.cursor.sequence == stream.committed
    finally:
        archive.close()
        stream.close()
