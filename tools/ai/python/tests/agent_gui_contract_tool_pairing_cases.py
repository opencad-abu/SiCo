# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent gui contract tool pairing contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sico_ui.transcript import Transcript


def test_transcript_collects_tool_receipts_into_one_counter():
    transcript = Transcript(username="system-user")
    transcript.receive({
        "kind": "task.started",
        "payload": {"origin": "chat", "text": "读取设置"},
    })
    transcript.receive({
        "kind": "model.status",
        "payload": {"phase": "tool_call", "label": "执行工具", "tool": "read_ade_setup", "turn": 1},
    })
    transcript.receive({
        "kind": "tool.started",
        "payload": {"id": "call-1", "name": "read_ade_setup"},
    })
    transcript.receive({
        "kind": "tool.finished",
        "payload": {
            "id": "call-1",
            "name": "read_ade_setup",
            "result": {"status": "ok", "summary": "已读取当前 ADE 设置"},
        },
    })
    assert transcript.activity is None
    assert [message["role"] for message in transcript.messages] == ["user", "tools"]
    group = transcript.messages[-1]
    assert group["text"] == "工具 ×1 · 成功 1"
    assert group["items"][0]["summary"] == "read_ade_setup：已读取当前 ADE 设置"


def test_tool_receipts_pair_by_call_id_and_finish_removes_waiting_status():
    transcript = Transcript()
    def receive(kind, payload):
        transcript.receive({"kind": kind, "task_id": "task", "payload": payload})

    receive("task.started", {"text": "查询"})
    for key in ("first", "second"):
        receive("model.status", {"phase": "tool_call", "label": "执行工具", "tool": "lookup"})
        receive("tool.started", {"id": key, "name": "lookup"})
    receive("tool.finished", {"id": "second", "name": "lookup", "result": {"summary": "second result"}})
    receive("model.status", {"label": "整理工具结果"})
    receive("tool.finished", {"id": "first", "name": "lookup", "result": {"summary": "first result"}})
    receive("model.status", {"label": "等待 LLM 响应"})
    receive("task.completed", {})
    assert [m["role"] for m in transcript.messages] == ["user", "tools"]
    group = transcript.messages[-1]
    assert group["text"] == "工具 ×2 · 成功 2"
    assert [item["summary"] for item in group["items"]] == [
        "lookup：first result", "lookup：second result"]


def test_tool_counter_groups_a_batch_and_opens_a_new_one_after_text():
    transcript = Transcript(username="system-user")
    def receive(kind, payload, **extra):
        transcript.receive({"kind": kind, "task_id": "task", "payload": payload, **extra})

    receive("task.started", {"origin": "chat", "text": "查一下"})
    for index in range(3):
        receive("tool.started", {"id": f"call-{index}", "name": "lookup"})
        receive("tool.finished", {"id": f"call-{index}", "name": "lookup",
                                  "result": {"status": "ok", "summary": "done"}})
    assert [m["role"] for m in transcript.messages] == ["user", "tools"]
    assert transcript.messages[-1]["text"] == "工具 ×3 · 成功 3"

    receive("model.delta", {"text": "先说一句"})
    receive("model.completed", {"text": "先说一句"})
    receive("tool.started", {"id": "call-late", "name": "lookup"})
    assert [m["role"] for m in transcript.messages] == ["user", "tools", "assistant", "tools"]
    assert transcript.messages[-1]["text"] == "工具 ×1 · 正在执行 lookup"


def test_tool_counter_surfaces_failures_and_unconfirmed_calls():
    transcript = Transcript(username="system-user")
    def receive(kind, payload, **extra):
        transcript.receive({"kind": kind, "task_id": "task", "payload": payload, **extra})

    receive("task.started", {"origin": "chat", "text": "跑一遍"})
    receive("tool.started", {"id": "ok", "name": "prepare_pdk_data"})
    receive("tool.finished", {"id": "ok", "name": "prepare_pdk_data",
                              "result": {"status": "ok", "summary": "已准备"}})
    receive("tool.started", {"id": "bad", "name": "prepare_circuit_library"})
    receive("tool.finished", {"id": "bad", "name": "prepare_circuit_library",
                              "result": {"status": "needs_reconcile", "summary": "结果未确认"}})
    receive("tool.started", {"id": "lost", "name": "inspect_schematic"})
    receive("task.cancelled", {"message": "Interrupted task abandoned without replay"})

    group = [m for m in transcript.messages if m["role"] == "tools"][-1]
    assert group["counts"] == {"total": 3, "ok": 1, "failed": 0, "needs_reconcile": 1,
                               "unconfirmed": 1}
    text = group["text"]
    assert "工具 ×3" in text and "成功 1" in text and "需核对 1" in text and "未确认 1" in text
    assert "prepare_circuit_library · 需核对 · 结果未确认" in text
    assert "inspect_schematic · 结果未确认" in text


def test_tool_counter_keeps_startup_context_reads_neutral():
    transcript = Transcript(username="system-user")
    transcript.receive({"kind": "task.started",
                        "payload": {"origin": "startup", "text": ""}})
    transcript.receive({"kind": "tool.started",
                        "payload": {"id": "ctx", "name": "get_context"}})
    transcript.receive({"kind": "tool.finished",
                        "payload": {"id": "ctx", "name": "get_context",
                                    "result": {"valid": True, "cellview": None}}})
    assert [m["role"] for m in transcript.messages] == ["context"]
    assert transcript.tools is None
