"""Original 0.154 public A2 events through an isolated Responses gateway."""

import json
import queue
import struct
import time
import zlib

import pytest
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
    registry,
    settings,
    sse,
)

from sico.codex.backend import CodexBackend
from sico.codex.rpc import AppServer
from sico.storage.journal import SessionJournal
from sico.storage.workbench import WorkbenchIndex


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(
            ">I", zlib.crc32(kind + data) & 0xffffffff,
        )

    rows = b"".join(b"\0" + bytes((40, 180, 100) if y < 32 else (220, 65, 95)) * 120
                    for y in range(64))
    header = chunk(b"IHDR", struct.pack(">IIBBBBB", 120, 64, 8, 2, 0, 0, 0))
    return b"\x89PNG\r\n\x1a\n" + header + (
        chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize("tool_format", ["native", "flat"])
@pytest.mark.parametrize("history_mode", ["legacy", "paginated"])
def test_real_web_and_resume(
    tmp_path, gateway, native_codex, tool_format, history_mode, monkeypatch,  # noqa: F811
):
    original_request = AppServer.request

    def request(self, method, params, **kwargs):
        if method == "thread/start":
            params = {**params, "historyMode": history_mode}
        return original_request(self, method, params, **kwargs)

    monkeypatch.setattr(AppServer, "request", request)
    gateway.responses.put(sse(
        {"id": "web-evidence", "type": "web_search_call", "status": "completed",
         "action": {"type": "search", "query": "project inspection reference"}},
        {"id": "reply", "type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "Evidence available."}]},
    ))
    provider = settings(gateway, native_codex, tool_format)
    with SessionJournal(tmp_path, "runtime-a2") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            events = list(backend.run("Collect public project evidence."))
            assert events[-1]["kind"] == "task.completed", events[-5:]
            thread = backend.runtime.rpc.request("thread/read", {
                "threadId": backend.thread_id, "includeTurns": history_mode == "legacy",
            })["thread"]
            if history_mode == "paginated":
                turns = list(backend.native_history.pages(
                    "thread/turns/list", time.monotonic() + 10, itemsView="notLoaded",
                ))
                for turn in turns:
                    turn["items"] = [e["item"] for e in backend.native_history.pages(
                        "thread/items/list", time.monotonic() + 10, turnId=turn["id"],
                    )]
                thread["turns"] = turns
            items = [i for t in thread["turns"] for i in t["items"]]
            (tmp_path / "native-items.json").write_text(json.dumps(items, indent=2))
            (tmp_path / "tool-definitions.json").write_text(json.dumps(
                gateway.requests[0]["tools"], indent=2,
            ))
            index = WorkbenchIndex(journal).sync()
            rows = [r for r in index.data.values() if r.get("native")]
            assert {r["native"]["item_type"] for r in rows} == {"webSearch"}
            for row in rows:
                item = next(i for i in items if i["id"] == row["native"]["item_id"])
                assert index.evidence_value(row["id"])["data"]["item"]["type"] == item["type"]
            before = len([e for e in journal.events() if e["kind"] == "codex.history.item"])
            backend.native_history.sync(thread)
            assert not any(e["kind"] == "codex.history.unavailable" for e in journal.events())
            assert len([e for e in journal.events() if e["kind"] == "codex.history.item"]) == before
        finally:
            backend.close()
    with SessionJournal(tmp_path, "runtime-a2") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            backend.resume_thread()
            assert len([e for e in journal.events() if e["kind"] == "codex.history.item"]) == before
            assert len(gateway.requests) == 1
        finally:
            backend.close()


def test_real_compaction_public_status(tmp_path, gateway, native_codex):  # noqa: F811
    gateway.responses.put(answer("Initial context."))
    gateway.responses.put(answer("Compact public conversation summary."))
    with SessionJournal(tmp_path, "runtime-compact") as journal:
        provider = settings(gateway, native_codex, "native")
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            assert list(backend.run("Review project context."))[-1]["kind"] == "task.completed"
            backend.runtime.rpc.request("thread/compact/start", {"threadId": backend.thread_id})
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    event = backend.runtime.rpc.next()
                except queue.Empty:
                    continue
                backend._notification(event)
                if event.get("method") == "turn/completed":
                    break
            rows = [r for r in WorkbenchIndex(journal).sync().data.values()
                    if r.get("native", {}).get("item_type") == "contextCompaction"]
            assert len(rows) == 1, journal.events()[-8:]
            assert rows[0]["status"] == "recorded"
            assert rows[0]["origin"] is None
        finally:
            backend.close()


def test_real_child_activity_keeps_parent_completion_separate(tmp_path, gateway, native_codex):  # noqa: F811
    def call(request, name, call_id, arguments):
        for group in request["tools"]:
            for tool in group.get("tools", []):
                if tool.get("name") == name:
                    return sse({"id": "fc_" + call_id, "call_id": call_id, "type": "function_call",
                                "namespace": group["name"], "name": name,
                                "arguments": json.dumps(arguments)})
        raise AssertionError("Native collaboration tool missing")

    def spawn(request):
        return call(request, "spawn_agent", "spawn-proof", {"task_name": "evidence_child",
                    "fork_turns": "none", "message": "Return the literal child evidence complete."})

    def reply(request):
        outputs = {i.get("call_id") for i in request["input"]
                   if i.get("type") == "function_call_output"}
        if "wait-proof" in outputs:
            return answer("Parent evidence complete.")
        if "spawn-proof" in outputs:
            return call(request, "wait_agent", "wait-proof", {"timeout_ms": 10000})
        return answer("Child evidence complete.")

    gateway.responses.put(spawn)
    for _ in range(8):
        gateway.responses.put(reply)
    provider = settings(gateway, native_codex, "native")
    with SessionJournal(tmp_path, "runtime-child") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            events = list(backend.run("For this test spawn one child to collect evidence."))
            assert events[-1]["kind"] == "task.completed", events[-8:]
            backend.poll_thread_names()
            rows = [r for r in WorkbenchIndex(journal).sync().data.values()
                    if r.get("native", {}).get("item_type") == "subAgentActivity"]
            assert rows, events[-10:]
            index = WorkbenchIndex(journal).sync()
            values = [index.evidence_value(row["id"])["item"] for row in rows]
            (tmp_path / "subagent-items.json").write_text(json.dumps(values, indent=2))
            assert any(i["kind"] == "started" for i in values)
            assert any(i["kind"] == "completed" for i in values)
            assert len({i["agentThreadId"] for i in values}) == 1
            assert all(i["agentThreadId"] != backend.thread_id for i in values)
            assert not any(e["kind"] == "tool.finished" and e["payload"].get("native")
                           for e in journal.events())
        finally:
            backend.close()
