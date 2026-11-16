"""Scale guards for long journals: tail-first replay and metadata caching.

These tests assert bounded *work* (records parsed, window offset, delivered
events) rather than wall-clock time, so they stay meaningful on a busy host.
The real-session numbers live in `.cad/ai/session-load-20260917/`.
"""

from __future__ import annotations

import json

import agent_sessions_harness as sessions_module
import pytest
from agent_sessions_harness import DesktopSessions

from sico.core.contracts import BoundContext, RunState
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.service.controller import SessionController
from sico.service.replay import journal_window_seat, prepare_preview
from sico.storage import journal as journal_module
from sico.storage import journal_records
from sico.storage.journal import BULK_KINDS
from sico.storage.history import CatalogIndex, SessionReader
from sico.storage.index import INDEX_NAME
from sico.storage.journal import SessionJournal
from sico.core.native_origin import NativeOrigins

CONTEXT = BoundContext("instance", "generation", "target", {})


def task_state(task_id="", status="completed"):
    task = {"id": task_id, "status": status, "pending_calls": []} if task_id else {}
    return RunState(CONTEXT, [], task)


def build_journal(root, session_id, *, tasks=4, answers=25, deleted=False):
    with SessionJournal(root, session_id) as journal:
        journal.append("session.created", {"context": CONTEXT.record()}, task_state())
        for index in range(tasks):
            task_id = f"task{index}"
            journal.append(
                "task.started",
                {"text": f"task {index}", "context": CONTEXT.record()},
                task_state(task_id, "executing"),
            )
            for answer in range(answers):
                journal.append("model.delta", {"text": f"delta {index}.{answer}"},
                               task_state(task_id, "executing"))
                journal.append("model.completed", {"text": f"answer {index}.{answer}"},
                               task_state(task_id, "executing"))
            journal.append("task.completed", {"status": "completed"}, task_state(task_id))
        if deleted:
            journal.append("codex.thread.deleted", {"thread_id": "thread"}, task_state())


def file_offsets(journal):
    """``sequence -> byte offset`` for a small journal, read forward."""

    offsets = {}
    with open(journal.directory / "events.jsonl", "rb") as stream:
        while True:
            offset = stream.tell()
            raw = stream.readline()
            if not raw or not raw.endswith(b"\n"):
                break
            offsets[json.loads(raw)["sequence"]] = offset
    return offsets


class FakeEvents:
    def __init__(self):
        self.stream = None

    def own(self, stream):
        self.stream = stream


class FakeSessions:
    """Enough of DesktopSessions for a read-only preview preparation."""

    def __init__(self, root):
        self.root, self.controllers, self.events = root, {}, FakeEvents()


def drain(stream):
    events = []
    while not stream.finished:
        batch = stream._publish()
        if batch.error:
            raise ValueError(batch.error)
        events.extend(batch.events)
    return events


def test_catalog_cache_skips_unchanged_journals_and_reads_only_appends(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "cached") as journal:
        journal.append("codex.thread.name", {"name": "first"}, task_state())
        parsed = []
        original = SessionReader.iter_raw

        def traced(reader, stream, **options):
            parsed.append((stream.tell(), options.get("sequence", 0)))
            yield from original(reader, stream, **options)

        monkeypatch.setattr(SessionReader, "iter_raw", traced)
        first = CatalogIndex(journal.root)
        assert first.scan()["cached"]["name"] == "first"
        assert len(parsed) == 1  # one pass over the whole journal

        # A fresh process (new index, same cache file) reads nothing at all.
        parsed.clear()
        second = CatalogIndex(journal.root)
        assert second.scan()["cached"]["name"] == "first"
        assert parsed == []

        # Only the appended records are parsed once the journal grows.
        sequence = len(journal.records)
        offset = (journal.directory / "events.jsonl").stat().st_size
        journal.append("model.delta", {"text": "tail" * 100}, task_state())
        journal.append("codex.thread.name", {"name": "second"}, task_state())
        assert second.scan()["cached"]["name"] == "second"
        assert parsed == [(offset, sequence)]
        assert second.scan()["cached"]["name"] == "second"
        assert parsed == [(offset, sequence)]


def test_catalog_cache_is_not_trusted_after_the_journal_changes(tmp_path):
    with SessionJournal(tmp_path, "cached") as journal:
        journal.append("codex.thread.name", {"name": "first"}, task_state())
        journal.append("model.delta", {"text": "x" * 400}, task_state())
        root, path = journal.root, journal.directory / "events.jsonl"
    assert CatalogIndex(root).scan()["cached"]["name"] == "first"

    # Shrink the journal (the cached offset no longer fits): full re-read.
    first_line = path.read_bytes().split(b"\n")[0] + b"\n"
    path.write_bytes(first_line)
    assert CatalogIndex(root).scan()["cached"]["name"] == "first"

    # A corrupt journal is reported instead of being served from the cache.
    path.write_bytes(b"broken\n")
    assert CatalogIndex(root).scan()["cached"].get("unavailable")


def test_tail_window_matches_replay_start_and_locates_the_boundary(tmp_path):
    build_journal(tmp_path, "long", tasks=4, answers=20)
    with SessionJournal(tmp_path, "long") as journal:
        reader = SessionReader(journal.root, "long")
        offsets = file_offsets(journal)
        total = len(journal.records)
        for messages in (1, 10, 40, 400):
            window = reader.tail_window(messages)
            assert window.start == journal.replay_start(messages)
            assert window.committed == total
            assert window.offset == offsets[window.start]
            assert window.deleted is False
            assert window.context["instance_id"] == CONTEXT.instance_id
        # The locator only walks the tail, not the whole journal.
        assert reader.tail_window(10).scanned < total


def test_tail_window_flags_a_deleted_session(tmp_path):
    build_journal(tmp_path, "gone", tasks=1, answers=2, deleted=True)
    with SessionJournal(tmp_path, "gone") as journal:
        assert SessionReader(journal.root, "gone").tail_window(5).deleted is True


def test_tail_window_uses_the_sidecar_index_and_refreshes_it(tmp_path, monkeypatch):
    build_journal(tmp_path, "long", tasks=4, answers=20)
    with SessionJournal(tmp_path, "long") as journal:
        reader = SessionReader(journal.root, "long")
        first = reader.tail_window(10)
        assert first.start == journal.replay_start(10)
        assert (journal.directory / INDEX_NAME).exists()

        # The index answers without scanning the journal from its end.
        def forbidden(*args, **kwargs):
            raise AssertionError("an exact index hit must not rescan the journal")

        monkeypatch.setattr(reader, "_scanned_window", forbidden)
        assert reader.tail_window(10).start == journal.replay_start(10)
        assert reader.tail_window(40).start == journal.replay_start(40)

        # Changed fingerprints rebuild safely; the refreshed window stays current.
        journal.append("task.started", {"text": "task 4", "context": CONTEXT.record()},
                       task_state("task4", "executing"))
        journal.append("model.completed", {"text": "answer 4.0"},
                       task_state("task4", "executing"))
        journal.append("task.completed", {"status": "completed"}, task_state("task4"))
        refreshed = reader.tail_window(1)
        assert refreshed.start == journal.replay_start(1)
        assert refreshed.committed == len(journal.records)


def test_indexed_origins_match_a_full_prefix_walk(tmp_path):
    with SessionJournal(tmp_path, "native") as journal:
        journal.append("session.created", {"context": CONTEXT.record()}, task_state())
        journal.append("codex.thread", {"thread_id": "thread-1"}, task_state())
        journal.append("task.started", {"text": "task", "context": CONTEXT.record()},
                       task_state("task0", "executing"))
        for name in ("lookup", "prepare"):
            payload = {
                "id": name, "name": name,
                "native": {"thread_id": "thread-1", "turn_id": "turn-1",
                           "item_id": name, "item_type": "commandExecution"},
            }
            journal.append("tool.started", payload, task_state("task0", "executing"))
            journal.append("tool.finished", {**payload, "result": {"status": "ok"}},
                           task_state("task0", "executing"))
        journal.append("model.completed", {"text": "answer"},
                       task_state("task0", "executing"))
        journal.append("task.completed", {"status": "completed"}, task_state("task0"))
        reader = SessionReader(journal.root, "native")
        last_offset = (journal.directory / "events.jsonl").stat().st_size

        expected = NativeOrigins()
        with open(journal.directory / "events.jsonl", "rb") as stream:
            for row, _offset, _length in reader.iter_raw_records(stream, validate=False):
                expected.observe(row)
        actual = reader.origins_before(last_offset)
        assert actual.thread_id == expected.thread_id == "thread-1"
        assert actual.tasks == expected.tasks
        assert actual.turns == expected.turns
        assert actual.turn_sequences == expected.turn_sequences
        assert set(actual.tasks) == {"task0"}


def test_index_rebuilds_after_the_journal_is_rewritten(tmp_path):
    with SessionJournal(tmp_path, "long") as journal:
        journal.append("session.created", {"context": CONTEXT.record()}, task_state())
        journal.append("codex.thread.name", {"name": "first"}, task_state())
        path = journal.directory / "events.jsonl"
        reader = SessionReader(journal.root, "long")
        assert reader.tail_window(1).committed == 2
        first_line = path.read_bytes().split(b"\n")[0] + b"\n"
        path.write_bytes(first_line)
        window = reader.tail_window(1)
        assert window.committed == 1
        assert window.start == 1
        assert window.context["instance_id"] == CONTEXT.instance_id


def test_tail_task_window_starts_at_the_last_task(tmp_path):
    build_journal(tmp_path, "long", tasks=5, answers=12)
    with SessionJournal(tmp_path, "long") as journal:
        reader = SessionReader(journal.root, "long")
        window = reader.tail_task_window()
        assert window.start == journal.replay_start(1)
        assert window.committed == len(journal.records)
        assert window.offset == file_offsets(journal)[window.start]
        assert window.context["instance_id"] == CONTEXT.instance_id
        assert window.deleted is False
        # The scan fallback (no index) agrees with the indexed locator.
        assert reader._scanned_task_window().start == window.start
        assert window.scanned < len(journal.records)


def test_prepare_preview_task_window_streams_the_last_task(tmp_path):
    build_journal(tmp_path, "long", tasks=5, answers=12)
    with SessionJournal(tmp_path, "long") as journal:
        reader = SessionReader(journal.root, "long")
        total = len(journal.records)
        expected = reader.tail_task_window()
        prepared = prepare_preview(FakeSessions(journal.root), "long", None, 1,
                                   task_window=True)
        assert prepared.stream.cursor.sequence == expected.start - 1
        delivered = drain(prepared.stream)
        assert delivered
        assert delivered[0]["kind"] == "task.started"
        assert delivered[0]["sequence"] == expected.start
        assert delivered[-1]["sequence"] <= total
        assert len(delivered) < total


def test_prepare_preview_streams_only_the_tail_window(tmp_path):
    build_journal(tmp_path, "long", tasks=4, answers=20)
    with SessionJournal(tmp_path, "long") as journal:
        reader = SessionReader(journal.root, "long")
        total = len(journal.records)
        start = reader.tail_window(10).start
        prepared = prepare_preview(FakeSessions(journal.root), "long", None, 1, 10)
        assert prepared.stream.cursor.sequence == start - 1
        delivered = drain(prepared.stream)
        assert delivered
        assert delivered[0]["sequence"] == start
        assert start <= delivered[-1]["sequence"] <= total
        assert len(delivered) < total

        # Without a window the legacy whole-journal walk still applies.
        legacy = prepare_preview(FakeSessions(journal.root), "long", None, 1)
        assert drain(legacy.stream)[0]["sequence"] == 1


def test_prepare_preview_rejects_a_deleted_tail_without_a_history_scan(tmp_path):
    build_journal(tmp_path, "gone", tasks=1, answers=2, deleted=True)
    with SessionJournal(tmp_path, "gone") as journal:
        with pytest.raises(ValueError, match="此会话已删除"):
            prepare_preview(FakeSessions(journal.root), "gone", None, 1, 5)


class StubLoop(PythonBackend):
    def __init__(self, provider, tools, journal, context, **_options):
        super().__init__(provider, tools, journal, context)


@pytest.fixture
def owned(tmp_path, monkeypatch):
    context = BoundContext("instance", "generation", "initial", {"valid": True})
    with SessionJournal(tmp_path, "history") as history:
        StubLoop(ScriptedProvider([]), ToolRegistry(), history, context)._event(
            "session.created", {})
    with SessionJournal(tmp_path, "initial") as journal:
        loop = StubLoop(ScriptedProvider([]), ToolRegistry(), journal, context)
        loop._event("session.created", {})
        sessions = DesktopSessions(SessionController(loop))
        monkeypatch.setattr(sessions_module, "create_backend", StubLoop)

        def forbidden(*args, **kwargs):
            raise AssertionError("open must not replay the whole journal")

        monkeypatch.setattr(sessions_module.SessionReader, "events", forbidden)
        try:
            yield sessions
        finally:
            sessions.close()
            sessions._close_controllers()
            assert sessions.wait(5)


def test_open_reads_the_deleted_marker_from_the_journal_not_a_history_scan(owned):
    opened = owned.open("history")
    assert opened.session_id == "history"


def test_open_rejects_a_deleted_session_without_a_history_scan(tmp_path, owned):
    with SessionJournal(tmp_path, "gone") as journal:
        journal.append("session.created", {"context": CONTEXT.record()}, task_state())
        journal.append("codex.thread.deleted", {"thread_id": "thread"},
                       task_state("task", "completed"))
    with pytest.raises(ValueError, match="此会话已删除"):
        owned.open("gone")


def build_tail_journal(root, session_id, *, lead=120, tail=0):
    """一个超长引子任务（可选再接一个尾任务），用于首屏行数上限测试。"""

    def state(task_id, status="executing"):
        return RunState(CONTEXT, [], {"id": task_id, "status": status, "pending_calls": []}
                        if task_id else {})

    rows = {"tail_start": 0}
    with SessionJournal(root, session_id) as journal:
        journal.append("session.created", {"context": CONTEXT.record()}, state(""))
        journal.append("task.started", {"text": "lead", "context": CONTEXT.record()},
                       state("lead"))
        for index in range(lead):
            journal.append("model.delta", {"text": f"lead {index}"}, state("lead"))
        journal.append("model.completed", {"text": "lead answer"}, state("lead"))
        journal.append("task.completed", {"status": "completed"}, state("lead", "completed"))
        if tail:
            rows["tail_start"] = len(journal.records) + 1
            journal.append("task.started", {"text": "tail", "context": CONTEXT.record()},
                           state("tail"))
            for index in range(tail):
                journal.append("model.delta", {"text": f"tail {index}"}, state("tail"))
            journal.append("model.completed", {"text": "tail answer"}, state("tail"))
            journal.append("task.completed", {"status": "completed"}, state("tail", "completed"))
    return rows


def test_bounded_window_starts_at_the_newest_task_inside_the_bound(tmp_path, monkeypatch):
    """上限内的最近任务起点成为首屏起点：任务完整、重播量封顶。"""

    rows = build_tail_journal(tmp_path, "bounded", lead=120, tail=20)
    monkeypatch.setattr(journal_module, "REPLAY_WINDOW_ROWS", 40)
    with SessionJournal(tmp_path, "bounded") as journal:
        reader = SessionReader(journal.root, "bounded")
        start = journal.replay_start(200)
        assert start == rows["tail_start"]
        assert journal.records[start - 1]["kind"] == "task.started"
        window = reader.tail_window(200)
        assert (window.start, window.offset) == (
            start, file_offsets(journal)[start])
        assert window.seat is None
        scanned = reader._scanned_window(200)
        assert (scanned.start, scanned.offset, scanned.seat) == (start, window.offset, None)
        # 整篇日志远大于上限，但首屏只覆盖上限内的内容。
        assert len(journal.records) - (start - 1) < 40


def test_bounded_window_seats_one_task_that_spans_the_whole_tail(tmp_path, monkeypatch):
    """单个任务横跨整段历史：窗口从上限处中途开始，并给出播种行。"""

    build_tail_journal(tmp_path, "giant", lead=140)
    monkeypatch.setattr(journal_module, "REPLAY_WINDOW_ROWS", 40)
    with SessionJournal(tmp_path, "giant") as journal:
        reader = SessionReader(journal.root, "giant")
        total = len(journal.records)
        start = journal.replay_start(200)
        assert start == total - 40 + 1                 # 上限行
        assert journal.records[start - 1]["kind"] != "task.started"
        window = reader.tail_window(200)
        assert (window.start, window.offset) == (start, file_offsets(journal)[start])
        assert window.seat == {"id": "lead", "status": "executing", "pending_calls": []}
        scanned = reader._scanned_window(200)
        assert (scanned.start, scanned.offset, scanned.seat) == (
            window.start, window.offset, window.seat)
        prepared = prepare_preview(FakeSessions(journal.root), "giant", None, 1, 200)
        assert prepared.stream.cursor.sequence == start - 1
        assert prepared.stream.cursor.task_id == "lead"      # 游标已按行状态播种
        delivered = drain(prepared.stream)
        # 投影会合并/丢弃窗口内的 delta，但事件都落在窗口内且远少于整篇。
        assert delivered and start <= delivered[0]["sequence"] <= total
        assert delivered[-1]["sequence"] <= total
        assert len(delivered) < total                  # 只重放上限内的窗口


def test_replay_resume_replays_the_captured_window(tmp_path, monkeypatch):
    """重连按捕获起点原样重放：任务中途的起点同样可续，游标按行播种。"""

    build_tail_journal(tmp_path, "giant", lead=140)
    monkeypatch.setattr(journal_module, "REPLAY_WINDOW_ROWS", 40)
    with SessionJournal(tmp_path, "giant") as journal:
        start, committed, _origins = journal.prepare_replay(200)
        assert journal.records[start - 1]["kind"] != "task.started"
        resumed, committed_again, _ = journal.prepare_replay(200, after=start - 1)
        assert (resumed, committed_again) == (start, committed)
        assert journal_window_seat(journal.records, start) == {
            "id": "lead", "status": "executing", "pending_calls": []}
        with pytest.raises(ValueError, match="precede the journal head"):
            journal.prepare_replay(200, after=len(journal.records))


def test_lazy_open_parses_only_the_tail_and_flagged_rows(tmp_path, monkeypatch):
    """惰性打开：展示噪声行不解析；按需读取、排除扫描与追加都走索引。"""

    def state(task_id, status="executing"):
        return RunState(CONTEXT, [], {"id": task_id, "status": status, "pending_calls": []}
                        if task_id else {})

    with SessionJournal(tmp_path, "bulk") as journal:
        journal.append("session.created", {"context": CONTEXT.record()}, state(""))
        journal.append("task.started", {"text": "go", "context": CONTEXT.record()},
                       state("bulk-task"))
        for index in range(5000):
            journal.append("model.delta", {"text": f"delta {index}"}, state("bulk-task"))
        journal.append("tool.started", {"id": "call", "name": "probe", "input": {}},
                       state("bulk-task"))
        journal.append("session.note", {"text": "note"}, state("bulk-task"))

    opened = []
    original = journal_records.strict_json

    def counted(raw):
        opened.append(raw[:1])
        return original(raw)

    monkeypatch.setattr(journal_records, "strict_json", counted)
    with SessionJournal(tmp_path, "bulk") as journal:
        assert len(journal.records) == 5004
        # 正常关闭已发布索引：再次打开只解析尾行/行类命中的少数行。
        assert len(opened) <= 8
        opened.clear()
        assert journal.records[-1]["kind"] == "session.note"     # 缓存命中
        assert opened == []
        assert journal.records[3]["kind"] == "model.delta"       # 按需单行（0 基）
        assert len(opened) == 1
        assert journal.latest_context()["instance_id"] == CONTEXT.instance_id
        assert [row["kind"] for row in journal.task_rows()] == ["task.started"]
        assert [row["kind"] for row in journal.events(exclude=BULK_KINDS)] == [
            "session.created", "task.started", "tool.started", "session.note"]
        assert journal.state.task["id"] == "bulk-task"
        batch, committed = journal.committed_batch(1, 3, 1 << 20)
        assert committed == 5004 and len(batch) == 3
        before = open(tmp_path / ".sico/ai/agent/sessions/bulk/events.jsonl", "rb").read()
        journal.append("session.note", {"text": "later"}, state("bulk-task"))
        assert len(journal.records) == 5005 and journal.records[-1]["payload"]["text"] == "later"
        assert len(journal.events(exclude=BULK_KINDS)) == 5
        assert open(tmp_path / ".sico/ai/agent/sessions/bulk/events.jsonl", "rb").read() != before
