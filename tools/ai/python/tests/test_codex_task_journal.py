"""Task journal keeps duplicate activity and unbounded task text out of the log."""

import threading
import time

from sico.codex.task_journal import TASK_TEXT_CHARS, TaskJournal
from sico.core.contracts import BoundContext, RunState
from sico.storage.journal import SessionJournal


def test_repeated_activity_writes_one_row(tmp_path):
    with SessionJournal(tmp_path, "journal") as journal:
        context = BoundContext("instance", "generation", "bound", {})
        state = RunState(context)
        state.task = {"id": "task", "status": "executing", "pending_calls": [], "text": ""}
        task = TaskJournal(journal, state, threading.RLock(), {}, lambda: time)
        for _ in range(3):
            task.activity("waiting_user", "等待答复：确认 PDK 数据更新")
        assert [row["kind"] for row in journal.events()] == ["model.status"]
        task.activity("waiting_model", "等待 LLM 响应")
        task.activity("waiting_user", "等待答复：确认 PDK 数据更新")
        assert [row["kind"] for row in journal.events()] == ["model.status"] * 3
        state.task["turn"] = 1
        task.activity("waiting_user", "等待答复：确认 PDK 数据更新")
        assert [row["kind"] for row in journal.events()] == ["model.status"] * 4


def test_task_text_keeps_a_bounded_head_and_tail(tmp_path):
    with SessionJournal(tmp_path, "journal") as journal:
        context = BoundContext("instance", "generation", "bound", {})
        state = RunState(context)
        state.task = {"id": "task", "status": "executing", "pending_calls": [], "text": ""}
        task = TaskJournal(journal, state, threading.RLock(), {}, lambda: time)
        task.append_delta("头" * 1000 + "中")
        task.append_delta("尾" * 4000)
        text = state.task["text"]
        assert len(text) == TASK_TEXT_CHARS
        assert text.startswith("头") and text.endswith("尾")
        assert [row["kind"] for row in journal.events()] == ["model.delta", "model.delta"]
        state.task["text"] = ""
        task.append_delta("已读取当前窗口。")
        assert state.task["text"] == "已读取当前窗口。"


def test_repeated_router_status_writes_one_row(tmp_path):
    with SessionJournal(tmp_path, "journal") as journal:
        context = BoundContext("instance", "generation", "bound", {})
        state = RunState(context)
        state.task = {"id": "task", "status": "executing", "pending_calls": [], "text": ""}
        task = TaskJournal(journal, state, threading.RLock(), {}, lambda: time)
        task.emit("router.status", {"request_id": "r", "state": "queued"})
        task.emit("router.status", {"request_id": "r", "state": "queued"})
        task.emit("router.status", {"request_id": "r", "state": "running"})
        assert [row["kind"] for row in journal.events()] == ["router.status"] * 2
