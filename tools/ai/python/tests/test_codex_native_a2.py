"""Public A2 state, historical provenance and recovery without task replay."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_codex_native_projection import CONTEXT, begin, command, emit

from sico.codex.backend import CodexBackend
from sico.core.contracts import BoundContext
from sico.core.tools import ToolRegistry
from sico.service.detail_content import DetailContent
from sico.core.session_events import SESSION_CONTRACT, EventCursor
from sico.storage.history import catalog_row
from sico.storage.journal import SessionJournal
from sico.storage.native_media import raster_data, web_url
from sico.storage.workbench import WorkbenchIndex


def web(**extra):
    return {"id": "search", "type": "webSearch", "query": "project reference",
            "action": {"type": "search", "query": "project reference"},
            "results": [{"title": "Evidence <one>", "url": "https://example.org/evidence",
                         "snippet": "literal <script>"}], **extra}


def batch(journal):
    return {"contract": SESSION_CONTRACT, "session_id": journal.session_id, "runtime_id": "runtime",
            "instance_id": "instance", "generation": "generation", "events": journal.events()}


@pytest.mark.parametrize("item,expected", [
    (web(), "ok"),
    ({"id": "image", "type": "imageGeneration", "status": "completed", "result": ""}, "ok"),
    ({"id": "image", "type": "imageGeneration", "status": "failed", "result": ""}, "tool_error"),
    ({"id": "image", "type": "imageGeneration", "status": "completed", "result": "",
      "failure": {"type": "usageLimitExceeded", "limitId": "image", "resetsAt": None}},
     "tool_error"),
])
def test_a2_receipts_and_public_field_filter(tmp_path, item, expected):
    with SessionJournal(tmp_path, "a2") as journal:
        backend = begin(journal)
        value = {**item, "reasoning": "hidden", "encrypted_content": "private"}
        emit(backend, "item/started", item=value)
        emit(backend, "item/completed", item=value)
        emit(backend, "item/completed", item=value)
        rows = [e for e in journal.events() if e["kind"] == "tool.finished"]
        assert len(rows) == 1 and rows[0]["payload"]["result"]["status"] == expected
        index = WorkbenchIndex(journal).sync()
        row = next(r for r in index.data.values() if r.get("native"))
        recorded = index.evidence_value(row["id"])["data"]["item"]
        assert "reasoning" not in recorded and "encrypted_content" not in recorded
        detail = DetailContent(index).prepare("data", row["id"])
        assert not detail["notice"]
        if item["type"] == "webSearch":
            assert "Evidence &lt;one&gt;" in detail["html"]
            assert "literal &lt;script&gt;" in detail["html"]
            assert detail["external_urls"] == ["https://example.org/evidence"]


def test_child_activity_is_not_child_completion_or_a_tool(tmp_path):
    with SessionJournal(tmp_path, "children") as journal:
        backend = begin(journal)
        item = {"type": "subAgentActivity", "id": "child-start", "kind": "started",
                "agentThreadId": "child", "agentPath": "/root/review"}
        emit(backend, "item/started", item=item)
        emit(backend, "item/completed", item=item)
        assert not backend.native.pending
        assert not any(e["kind"] == "tool.finished" for e in journal.events())
        assert catalog_row("children", 0, journal.events())["children"][0]["status"] == "started"
        backend.native_history.bind("turn")
        backend._finish("completed")
        late = {"method": "item/completed", "params": {"threadId": "thread", "turnId": "turn",
                "item": {**item, "id": "child-complete", "kind": "completed"}}}
        assert backend._notification(late)
        assert backend.state.task["status"] == "completed"
        assert catalog_row("children", 0, journal.events())["children"][0]["status"] == "completed"
        count = len(journal.events())
        assert backend._notification(late)
        assert len(journal.events()) == count
        EventCursor("children", "runtime", CONTEXT).consume(batch(journal))


@pytest.mark.parametrize("order", [("legacy", "item"), ("item", "legacy"), ("legacy",)])
def test_compaction_legacy_notification_is_not_double_counted(tmp_path, order):
    with SessionJournal(tmp_path, "compact") as journal:
        backend = begin(journal)
        for source in order:
            if source == "legacy":
                emit(backend, "thread/compacted")
            else:
                emit(backend, "item/completed", item={"id": "compact", "type": "contextCompaction"})
        backend._finish("completed")
        rows = [r for r in WorkbenchIndex(journal).sync().data.values() if r.get("native")]
        assert len(rows) == 1
        assert rows[0]["status"] == "recorded"
        assert not any(e["kind"] == "tool.finished" for e in journal.events())
        backend.native_history.capture("turn", {"id": "compact", "type": "contextCompaction"},
                                       observed=True)
        assert len([r for r in WorkbenchIndex(journal).sync().data.values()
                    if r.get("native")]) == (2 if order == ("legacy",) else 1)


def test_legacy_compaction_receipt_does_not_hide_later_real_items(tmp_path):
    items = [{"id": key, "type": "contextCompaction"} for key in ("first", "second")]
    with SessionJournal(tmp_path, "legacy-history") as journal:
        backend = begin(journal)
        emit(backend, "thread/compacted")
        backend._finish("completed")
        for item in items:
            backend.native_history.capture("turn", item, observed=True)
        count = len(journal.events())
    with SessionJournal(tmp_path, "legacy-history") as journal:
        backend = CodexBackend(SimpleNamespace(), ToolRegistry(), journal, CONTEXT)
        for item in items:
            backend.native_history.capture("turn", item, observed=True)
        backend.native_history.notification({
            "method": "thread/compacted", "params": {"threadId": "thread", "turnId": "turn"},
        })
        assert len(journal.events()) == count
        rows = [r for r in WorkbenchIndex(journal).sync().data.values() if r.get("native")]
        assert {r["native"]["item_id"] for r in rows} == {"compacted_turn", "first", "second"}
        assert not any(e["kind"] == "tool.finished" for e in journal.events())


def test_history_restores_original_target_and_leaves_unknown_turn_unassigned(tmp_path):
    with SessionJournal(tmp_path, "history") as journal:
        backend = begin(journal)
        backend.native_history.bind("old-turn")
        original = deepcopy(backend.state.task)
        backend._finish("cancelled")
        other = BoundContext("instance", "generation", "different_target", {"valid": True})
        backend._begin("New task", other, "new-input", "chat")
        backend.native_history.bind("new-turn")
        current = deepcopy(backend.state.record())
        backend.native_history.capture_thread({"id": "thread", "turns": [
            {"id": "old-turn", "status": "completed", "completedAt": 123, "items": [web()]},
            {"id": "unknown-turn", "status": "completed", "items": [command()]},
        ]})
        assert backend.state.record() == current
        index = WorkbenchIndex(journal).sync()
        rows = [r for r in index.data.values() if r["data_type"] == "codex_history"]
        old = next(r for r in rows if r["native"]["turn_id"] == "old-turn")
        assert old["task_id"] == original["id"]
        assert old["origin"]["context"] == CONTEXT.record()
        unknown = next(r for r in rows if r["native"]["turn_id"] == "unknown-turn")
        assert unknown["task_id"] == "" and unknown["origin"] is None
        assert unknown["parents"] == []
        assert "未归属" in DetailContent(index).prepare("data", unknown["id"])["html"]
        assert index.executions[original["id"]]["status"] == "cancelled"
        EventCursor("history", "runtime", CONTEXT).consume(batch(journal))
        tampered = deepcopy(batch(journal))
        historical = next(e for e in tampered["events"] if e["kind"] == "codex.history.item")
        historical["payload"]["origin"]["context"] = other.record()
        with pytest.raises(ValueError, match="origin"):
            EventCursor("history", "runtime", CONTEXT).consume(tampered)


def test_history_restart_deduplicates_and_can_confirm_missing_completion(tmp_path):
    with SessionJournal(tmp_path, "restore") as journal:
        backend = begin(journal)
        emit(backend, "item/started", item=web())
        backend._finish("needs_reconcile")
        task = backend.state.task["id"]
    with SessionJournal(tmp_path, "restore") as journal:
        backend = CodexBackend(SimpleNamespace(), ToolRegistry(), journal, CONTEXT)
        state = backend.state.record()
        thread = {"id": "thread", "turns": [
            {"id": "turn", "status": "completed", "items": [web()]},
        ]}
        backend.native_history.capture_thread(thread)
        assert backend.state.record() == state
        repaired = [r for r in WorkbenchIndex(journal).sync().data.values()
                    if r["data_type"] == "codex_history"]
        assert len(repaired) == 1 and repaired[0]["status"] == "ok"
        assert repaired[0]["task_id"] == task
        count = len(journal.events())
        backend.native_history.capture_thread(thread)
        assert len(journal.events()) == count
    with SessionJournal(tmp_path, "restore") as journal:
        backend = CodexBackend(SimpleNamespace(), ToolRegistry(), journal, CONTEXT)
        count = len(journal.events())
        backend.native_history.capture_thread(thread)
        assert len(journal.events()) == count
        assert backend.runtime is None


def test_history_pagination_is_read_only_and_bounded(tmp_path):
    with SessionJournal(tmp_path, "pages") as journal:
        backend = begin(journal)
        requests = []

        def request(method, params, **kwargs):
            requests.append((method, params))
            if method == "thread/items/list":
                return {"data": [{"turnId": "old", "item": web()}]}
            return {"data": [{"id": "old", "status": "completed", "itemsView": "full",
                              "items": [web()]}], "nextCursor": "loop"}

        backend.runtime = SimpleNamespace(rpc=SimpleNamespace(request=request))
        backend.native_history.sync({"id": "thread", "historyMode": "paginated", "turns": []})
        assert len(requests) == 4
        assert all(method in {"thread/turns/list", "thread/items/list"} for method, _ in requests)
        assert journal.events()[-1]["kind"] == "codex.history.unavailable"
        assert len([e for e in journal.events() if e["kind"] == "codex.history.item"]) == 1


def test_two_compactions_in_one_turn_remain_distinct(tmp_path):
    with SessionJournal(tmp_path, "two-compactions") as journal:
        backend = begin(journal)
        backend.native_history.bind("turn")
        for key in ("first", "second"):
            emit(backend, "item/completed", item={"id": key, "type": "contextCompaction"})
            emit(backend, "thread/compacted")
        backend._finish("completed")
        backend.native_history.capture("turn", {"id": "third", "type": "contextCompaction"},
                                       observed=True)
        rows = [r for r in WorkbenchIndex(journal).sync().data.values() if r.get("native")]
        assert len(rows) == 3


def test_legacy_completion_does_not_confirm_an_unfinished_compaction_item(tmp_path):
    with SessionJournal(tmp_path, "partial-compact") as journal:
        backend = begin(journal)
        emit(backend, "item/started", item={"id": "compact", "type": "contextCompaction"})
        emit(backend, "thread/compacted")
        backend._finish("completed")
        rows = [r for r in WorkbenchIndex(journal).sync().data.values() if r.get("native")]
        assert len(rows) == 2 and rows[-1]["status"] == "needs_reconcile"
        assert rows[0]["status"] == "executing"


def test_tail_cursor_retains_original_turn_origin_and_failed_batch_is_atomic(tmp_path):
    with SessionJournal(tmp_path, "tail") as journal:
        backend = begin(journal)
        backend.native_history.bind("old")
        backend._finish("completed")
        end = len(journal.events())
        backend._begin("Latest task", CONTEXT, "latest", "chat")
        backend.native_history.capture("old", web(), observed=True)
        cursor = EventCursor("tail", "runtime", CONTEXT)
        cursor.sequence = end
        cursor.native_origins = journal.native_origins(end)
        updates = {**batch(journal), "events": journal.events(end)}
        bad = deepcopy(updates)
        bad["events"][-1]["payload"]["origin"]["sequence"] = 999
        with pytest.raises(ValueError, match="origin"):
            cursor.consume(bad)
        assert cursor.sequence == end and cursor.task_id == ""
        assert len(cursor.native_origins.tasks) == 1
        cursor.consume(updates)


def test_partial_history_is_not_accepted_as_full_evidence(tmp_path):
    with SessionJournal(tmp_path, "partial-history") as journal:
        backend = begin(journal)
        requests = []

        def request(method, params, **kwargs):
            requests.append(method)
            if method == "thread/turns/list":
                return {"data": [{"id": "old", "status": "completed", "items": [],
                                  "itemsView": "notLoaded"}]}
            return {"data": [{"turnId": "wrong", "item": web()}]}

        backend.runtime = SimpleNamespace(rpc=SimpleNamespace(request=request))
        backend.native_history.sync({"id": "thread", "turns": [{"id": "old",
            "status": "completed", "itemsView": "summary", "items": [command()]}]})
        assert requests == ["thread/turns/list", "thread/items/list"]
        assert not any(e["kind"] == "codex.history.item" for e in journal.events())
        assert journal.events()[-1]["kind"] == "codex.history.unavailable"


def test_history_stage_comes_from_original_turn_not_latest_selection(tmp_path):
    with SessionJournal(tmp_path, "stage-history") as journal:
        backend = begin(journal)
        work = {"id": "work", "title": "Evidence"}
        backend._event("workbench.stage.selected", {"work": work, "stage": {
            "id": "first", "work_id": "work", "stage_title": "First",
        }})
        backend.native_history.bind("old")
        backend._event("workbench.stage.selected", {"work": work, "stage": {
            "id": "second", "work_id": "work", "stage_title": "Second",
        }})
        backend._finish("completed")
        backend.native_history.capture("old", web(), observed=True)
        index = WorkbenchIndex(journal).sync()
        row = next(r for r in index.data.values() if r["data_type"] == "codex_history")
        assert row["stage_id"] == "first" and row["work_id"] == "work"
        reference = [{"id": row["id"], "digest": row["digest"]}]
        index.verify_evidence(reference)
        row["task_id"] = "different"
        with pytest.raises(ValueError, match="task"):
            index.verify_evidence(reference)


def test_history_does_not_downgrade_confirmed_items_or_change_types(tmp_path):
    with SessionJournal(tmp_path, "confirmed") as journal:
        backend = begin(journal)
        emit(backend, "item/completed", item=web())
        backend._finish("completed")
        count = len(journal.events())
        backend.native_history.capture("turn", web(results=None), observed=False)
        assert len(journal.events()) == count
        with pytest.raises(ValueError, match="identity changed"):
            backend.native_history.capture("turn", command(id="search"), observed=True)
        backend._begin("Next", CONTEXT, "next", "chat")
        with pytest.raises(ValueError, match="another task"):
            backend.native_history.bind("turn")


def test_long_search_results_remain_archived_and_checksum_verified(tmp_path):
    with SessionJournal(tmp_path, "long-search") as journal:
        backend = begin(journal)
        item = web(results=[{"url": "https://example.org/" + str(i), "title": str(i),
                            "text": "public evidence " * 2000} for i in range(110)])
        emit(backend, "item/completed", item=item)
        backend._finish("completed")
        index = WorkbenchIndex(journal).sync()
        row = next(r for r in index.data.values() if r.get("native"))
        assert row["value"]["truncated"]
        assert index.evidence_value(row["id"])["data"]["item"]["results"] == item["results"]
        detail = DetailContent(index).prepare("data", row["id"])
        assert "当前显示部分内容" in detail["html"]
        assert len(detail["html"]) < 75000
        artifact = row["value"]["artifact"]
        (journal.root / artifact["path"]).write_text("broken archive")
        damaged = DetailContent(index).prepare("data", row["id"])
        assert damaged["notice"] and not damaged["external_urls"]


def test_foreign_and_hidden_history_is_not_copied(tmp_path):
    with SessionJournal(tmp_path, "filtered-history") as journal:
        backend = begin(journal)
        before = len(journal.events())
        backend.native_history.capture_thread({"id": "thread", "turns": [{"id": "old",
            "status": "completed", "items": [
                {"id": "r", "type": "reasoning", "text": "hidden"},
                {"id": "m", "type": "mcpToolCall", "result": "already recorded"},
            ]}]})
        assert len(journal.events()) == before
        assert not backend._notification({"method": "item/completed", "params": {
            "threadId": "foreign", "turnId": "old", "item": web(),
        }})
        with pytest.raises(ValueError, match="another thread"):
            backend.native_history.capture_thread({"id": "foreign", "turns": []})


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)",
                                  "https://user:password@example.org", "https://example.org\n"])
def test_untrusted_urls_and_non_raster_media_are_not_opened(url):
    assert web_url(url) is None
    assert raster_data(url) is None
    assert raster_data("data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=") is None
