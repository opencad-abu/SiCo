"""Native receipts, public plans and diffs preserve identity and archived evidence."""

from __future__ import annotations

import json
from collections import deque
from copy import deepcopy
from types import SimpleNamespace

import pytest

from sico.codex.backend import CodexBackend
from sico.codex.rpc import RpcError
from sico.core.contracts import BoundContext
from sico.core.tools import ToolRegistry
from sico.service.detail_content import DetailContent
from sico.core.session_events import SESSION_CONTRACT, EventCursor
from sico.storage.journal import SessionJournal
from sico.storage.workbench import WorkbenchIndex

CONTEXT = BoundContext("instance", "generation", "target", {"valid": True})


def begin(journal):
    backend = CodexBackend(SimpleNamespace(), ToolRegistry(), journal, CONTEXT)
    from codex_thread_fixture import bind_thread
    bind_thread(backend)
    backend._begin("Review project files", CONTEXT, "input", "chat")
    return backend


def emit(backend, method, *, item=None, thread="thread", turn="turn", **params):
    if item is not None:
        params["item"] = item
    return backend.native.receive(method, {"threadId": thread, "turnId": turn, **params}, "turn")


def command(**fields):
    return {"id": "command", "type": "commandExecution", "command": "printf evidence",
            "cwd": "/tmp", "status": "completed", "exitCode": 0, **fields}


@pytest.mark.parametrize("kind", ["commandExecution", "fileChange"])
@pytest.mark.parametrize("status,exit_code,expected", [
    ("completed", 0, "ok"), ("completed", 3, "tool_error"),
    ("failed", 0, "tool_error"), ("declined", None, "tool_error"),
    ("inProgress", None, "needs_reconcile"), ("completed", None, "needs_reconcile"),
])
def test_terminal_receipts_and_duplicate_items(tmp_path, kind, status, exit_code, expected):
    with SessionJournal(tmp_path, "receipts") as journal:
        backend = begin(journal)
        item = command(type=kind, status=status, exitCode=exit_code)
        if kind == "fileChange" and status == "completed":
            expected = "ok"
        emit(backend, "item/completed", item=item)
        emit(backend, "item/completed", item=item)
        emit(backend, "item/started", item=item)
        emit(backend, "item/commandExecution/outputDelta", itemId="command", delta="late")
        starts = [e for e in journal.events() if e["kind"] == "tool.started"]
        ends = [e for e in journal.events() if e["kind"] == "tool.finished"]
        assert len(starts) == len(ends) == 1
        assert ends[0]["payload"]["result"]["status"] == expected
        assert starts[0]["payload"]["native"] == ends[0]["payload"]["native"]
        assert not backend.native.pending
        index = WorkbenchIndex(journal).sync()
        row = index.data[f"d_{ends[0]['sequence']}_tool"]
        assert row["category"] == "process"
        assert row["task_id"] == backend.state.task["id"]
        assert row["parents"]


def test_long_output_and_diff_are_lossless_and_deduplicated(tmp_path):
    with SessionJournal(tmp_path, "output") as journal:
        backend = begin(journal)
        emit(backend, "item/started", item=command(status="inProgress", exitCode=None))
        output = "first<untrusted>\n" * 15000
        emit(backend, "item/commandExecution/outputDelta", itemId="command", delta=output)
        emit(backend, "item/completed", item=command(aggregatedOutput=output))
        diff = "+changed\n" * 18000
        emit(backend, "turn/diff/updated", diff=diff)
        size = len(journal.events())
        emit(backend, "turn/diff/updated", diff=diff)
        assert len(journal.events()) == size
        index = WorkbenchIndex(journal).sync()
        rows = list(index.data.values())
        tool = next(r for r in rows if r.get("tool") == "codex_command")
        assert tool["value"]["truncated"]
        data = index.evidence_value(tool["id"])["data"]
        assert data["item"]["aggregatedOutput"] == output
        assert "".join(json.loads(journal.artifact_text(a["path"], a["sha256"]))["text"]
                       for a in data["output_chunks"]) == output
        diff_row = next(r for r in rows if r["data_type"] == "codex_diff")
        assert index.evidence_value(diff_row["id"])["diff"] == diff
        assert len(json.dumps(diff_row["value"])) < 1024
        detail = DetailContent(index).prepare("data", tool["id"])
        # 输出走结构化字段（下方文本区）：正文不再包含输出正文，只保留字段表与截断提示。
        assert "<untrusted>" in detail["output"] and detail["output_truncated"]
        assert detail["output"].endswith("first<untrusted>\n")
        assert len(detail["output"]) < len(output)
        assert "&lt;untrusted&gt;" not in detail["html"]
        assert "当前显示部分内容" in detail["html"]
        assert not detail["notice"]
        artifact = tool["value"]["artifact"]
        (journal.root / artifact["path"]).write_text("tampered")
        assert "校验失败" in DetailContent(index).prepare("data", tool["id"])["notice"]


def test_plan_completion_is_authoritative_and_replay_is_stable(tmp_path):
    with SessionJournal(tmp_path, "plans") as journal:
        backend = begin(journal)
        emit(backend, "turn/plan/updated", plan=[{"step": "Inspect", "status": "pending"}])
        emit(backend, "item/plan/delta", itemId="plan", delta="provisional text")
        emit(backend, "item/completed", item={"id": "plan", "type": "plan", "text": "Revised plan"})
        emit(backend, "turn/plan/updated", plan=[{"step": "Inspect", "status": "completed"}])
        backend._finish("completed")
        index = WorkbenchIndex(journal).sync()
        plan = [r for r in index.data.values() if r["data_type"] == "codex_plan"]
        assert len(plan) == 3
        final = next(r for r in plan if r["native"]["item_id"] == "plan")
        html = DetailContent(index).prepare("data", final["id"])["html"]
        assert "Revised plan" in html
        assert "provisional text" not in html
        before = deepcopy(index.data)
        assert index.sync().data == before
        assert WorkbenchIndex(journal).sync().data == before


def test_interrupted_items_survive_restart_without_reexecution(tmp_path):
    with SessionJournal(tmp_path, "restart") as journal:
        backend = begin(journal)
        emit(backend, "item/started", item=command(status="inProgress", exitCode=None))
        emit(backend, "item/commandExecution/outputDelta", itemId="command", delta="partial")
        backend.native.flush(force=True)
        task_id = backend.state.task["id"]
    with SessionJournal(tmp_path, "restart") as journal:
        backend = CodexBackend(None, ToolRegistry(), journal, CONTEXT)
        assert backend.runtime is None
        assert backend.state.task["status"] == "needs_reconcile"
        rows = [r for r in WorkbenchIndex(journal).sync().data.values() if r.get("native")]
        end = next(r for r in rows if r["data_type"] == "tool")
        assert end["status"] == "needs_reconcile" and end["task_id"] == task_id
        detail = DetailContent(WorkbenchIndex(journal).sync()).prepare("data", end["id"])
        assert "partial" in detail["output"] and not detail["output_truncated"]
        assert "printf evidence" in detail["html"]
        assert "未收到操作完成回执" in detail["html"]


def test_unknown_reasoning_mcp_and_foreign_notifications_are_not_projected(tmp_path):
    with SessionJournal(tmp_path, "filtered") as journal:
        backend = begin(journal)
        before = journal.events()
        for item in (
            {"id": "private", "type": "reasoning", "text": "SECRET"},
            {"id": "mcp", "type": "mcpToolCall", "server": "sico_ic", "tool": "get_context"},
            {"id": "new", "type": "futureOperation", "text": "SECRET"},
        ):
            emit(backend, "item/started", item=item)
            emit(backend, "item/completed", item=item)
        emit(backend, "item/completed", item=command(), thread="child")
        emit(backend, "item/completed", item=command(), turn="old")
        emit(backend, "item/completed", item=command(), turn=None)
        assert journal.events() == before


def test_cursor_allows_idle_names_but_rejects_late_native_task_events(tmp_path):
    with SessionJournal(tmp_path, "cursor") as journal:
        backend = begin(journal)
        emit(backend, "turn/diff/updated", diff="+data")
        backend._finish("completed")
        backend._event("codex.thread.name", {"name": "Review", "thread_id": "thread"})
        cursor = EventCursor("cursor", "runtime", CONTEXT)
        batch = {"contract": SESSION_CONTRACT, "session_id": "cursor", "runtime_id": "runtime",
                 "instance_id": "instance", "generation": "generation", "events": journal.events()}
        cursor.consume(batch)
        late = backend._event("codex.diff.updated", {})
        with pytest.raises(ValueError, match="Stale task"):
            cursor.consume({**batch, "events": [late]})


@pytest.mark.parametrize("terminal", ["completed", "interrupted", "disconnect"])
def test_turn_end_preserves_unfinished_native_receipt(tmp_path, terminal):
    with SessionJournal(tmp_path, "turn-end") as journal:
        backend = begin(journal)
        events = deque([{"method": "item/started", "params": {
            "threadId": "thread", "turnId": "turn",
            "item": command(status="inProgress", exitCode=None),
        }}, {"method": "item/commandExecution/outputDelta", "params": {
            "threadId": "thread", "turnId": "turn", "itemId": "command", "delta": "received",
        }}])
        if terminal != "disconnect":
            events.append({"method": "turn/completed", "params": {
                "threadId": "thread", "turnId": "turn", "turn": {"id": "turn", "status": terminal},
            }})
        requests = []

        def request(method, params, **kwargs):
            requests.append(method)
            return {}

        def next_event():
            if events:
                return events.popleft()
            raise RpcError("disconnected")

        backend.runtime = SimpleNamespace(
            rpc=SimpleNamespace(next=next_event, request=request), close=lambda: None,
        )
        if terminal == "disconnect":
            with pytest.raises(RpcError):
                list(backend._turn("turn", lambda: False))
            backend.close()
            backend._finish("needs_reconcile")
        else:
            list(backend._turn("turn", lambda: False))
        assert not backend.native.pending
        ends = [e for e in journal.events() if e["kind"] == "tool.finished"]
        assert len(ends) == 1 and ends[0]["payload"]["result"]["status"] == "needs_reconcile"
        assert backend.state.task["status"] == "needs_reconcile"
        assert "turn/start" not in requests and "turn/steer" not in requests
        backend.close()


def test_report_verifies_all_chunks_and_native_identity(tmp_path):
    with SessionJournal(tmp_path, "verify") as journal:
        backend = begin(journal)
        emit(backend, "item/started", item=command(status="inProgress", exitCode=None))
        emit(backend, "item/commandExecution/outputDelta", itemId="command", delta="evidence")
        emit(backend, "item/completed", item=command(aggregatedOutput="evidence"))
        index = WorkbenchIndex(journal).sync()
        row = next(r for r in index.data.values() if r.get("tool") == "codex_command")
        reference = [{"id": row["id"], "digest": row["digest"]}]
        index.verify_evidence(reference)
        row["native"]["turn_id"] = "wrong-turn"
        with pytest.raises(ValueError, match="identity"):
            index.evidence_value(row["id"])
        row["native"]["turn_id"] = "turn"
        value = index.evidence_value(row["id"])
        artifact = value["data"]["output_chunks"][0]
        (journal.root / artifact["path"]).write_text("corrupted")
        with pytest.raises(ValueError, match="checksum"):
            index.verify_evidence(reference)


def test_captured_output_keeps_the_recent_tail_and_flags_truncation():
    """输出进下方文本区：超长取最近的尾部，并带截断标记（计划文档 §4.4）。"""

    from sico.service.native_display import OUTPUT_CHARS, output_text

    class Reader:
        def __init__(self, texts=()):
            self.texts = dict(texts)

        def artifact_text(self, path, sha256):
            return json.dumps({"text": self.texts[path]})

    final = "head" + "x" * (OUTPUT_CHARS + 5) + "tail"
    text, limited = output_text({"aggregatedOutput": final}, {}, Reader())
    assert limited and text == final[-OUTPUT_CHARS:]

    assert output_text({"aggregatedOutput": "done"}, {}, Reader()) == ("done", False)
    # 条目自带 text 时不重复展示输出分块。
    assert output_text({"text": "answer"},
                       {"output_chunks": [{"path": "0", "sha256": "sha"}]}, Reader()) == ("", False)

    chunks = [{"path": str(i), "sha256": "sha"} for i in range(3)]
    reader = Reader({str(i): "z" * 40000 for i in range(3)})
    text, limited = output_text({}, {"output_chunks": chunks}, reader)
    assert limited and text == "z" * OUTPUT_CHARS
    small = Reader({str(i): "chunk " + str(i) for i in range(3)})
    assert output_text({}, {"output_chunks": chunks}, small) == ("chunk 0chunk 1chunk 2", False)
