"""Real 0.154 native commands and patches against a controlled localhost gateway."""

from __future__ import annotations

import json

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
from sico.storage.journal import SessionJournal
from sico.storage.workbench import WorkbenchIndex


def execute(cmd, key):
    def reply(request):
        for group in request["tools"]:
            for tool in group.get("tools", [group]):
                if tool.get("name") == "exec_command":
                    item = {"type": "function_call", "id": "fc_" + key, "call_id": key,
                            "name": "exec_command", "arguments": json.dumps({
                                "cmd": cmd, "yield_time_ms": 1000, "max_output_tokens": 1000,
                                "login": False,
                            })}
                    if group.get("type") == "namespace":
                        item["namespace"] = group["name"]
                    return sse(item)
        raise AssertionError("Native command tool not exposed")
    return reply


@pytest.mark.parametrize("tool_format", ["native", "flat"])
def test_native_command_patch_and_resume_evidence(tmp_path, gateway, native_codex, tool_format):  # noqa: F811
    target = tmp_path / "native-proof.txt"
    patch = "*** Begin Patch\n*** Add File: native-proof.txt\n+native evidence\n*** End Patch"
    for response in (
        execute("printf native-command-evidence", "shell"),
        execute("apply_patch <<'PATCH'\n" + patch + "\nPATCH", "patch"),
        answer("Files reviewed."),
        answer("Continue from existing history."),
    ):
        gateway.responses.put(response)
    provider = settings(gateway, native_codex, tool_format)
    with SessionJournal(tmp_path, "native-evidence") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            events = list(backend.run("Run the authorized project evidence task."))
            assert events[-1]["kind"] == "task.completed", events[-5:]
            assert target.read_text() == "native evidence\n"
            thread_id = backend.thread_id
            native_thread = backend.runtime.rpc.request("thread/read", {
                "threadId": thread_id, "includeTurns": True,
            })["thread"]
            items = [i for t in native_thread["turns"] for i in t["items"]]
            command = next(i for i in items if i["type"] == "commandExecution"
                           and "native-command-evidence" in i["command"])
            rows = list(WorkbenchIndex(journal).sync().data.values())
            command_row = next(r for r in rows if r["data_type"] == "tool"
                               and r.get("native", {}).get("item_id") == command["id"])
            assert command_row["status"] == "ok"
            value = WorkbenchIndex(journal).sync().evidence_value(command_row["id"])
            assert value["data"]["item"]["exitCode"] == command["exitCode"] == 0
            assert value["data"]["item"]["aggregatedOutput"] == command["aggregatedOutput"]
            assert any(r.get("tool") == "codex_file_change" for r in rows), items
            assert any(r["data_type"] == "codex_diff" for r in rows), items
            receipts = [r["id"] for r in rows if r.get("native") and r["data_type"] == "tool"]
            mcp = [e for e in events if e["kind"] == "tool.finished"
                   and e["payload"]["name"] == "get_context"]
            assert len(mcp) == 1
        finally:
            backend.close()
    with SessionJournal(tmp_path, "native-evidence") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            assert list(backend.run("Continue review."))[-1]["kind"] == "task.completed"
            assert backend.thread_id == thread_id
            rows = list(WorkbenchIndex(journal).sync().data.values())
            restored = [r["id"] for r in rows if r.get("native") and r["data_type"] == "tool"]
            assert restored == receipts
            assert len(gateway.requests) == 4
        finally:
            backend.close()
