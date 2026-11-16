"""Process index, pagination and bounded detail/output projection."""

from __future__ import annotations

import json
import time
from collections import deque
from types import SimpleNamespace

import pytest

from sico.service import process_data


class Reader:
    directory = "/project"

    def __init__(self, artifacts=None):
        self.artifacts = artifacts or {}

    def artifact_text(self, path, _digest):
        return self.artifacts[path]


def test_timestamp_when_elapsed_and_command_name_are_bounded():
    assert process_data.timestamp("2026-01-02T03:04:05+00:00") is not None
    assert process_data.timestamp("bad") is None
    assert process_data.when(None) == "未提供"
    assert process_data.when(time.time()).count(":") == 2
    assert process_data.elapsed({}) == "未提供"
    assert process_data.elapsed({"started_at": 10, "state": "running"}) != "未确认"
    assert process_data.elapsed({"started_at": 10, "state": "exited"}) == "未确认"
    assert process_data.elapsed({"started_at": 10, "finished_at": 11, "state": "exited"}) == "1秒"
    assert process_data.command_name("/usr/bin/bash -lc x") == "bash"
    assert process_data.command_name("") == "shell"
    assert process_data.command_name("'") == "shell"


def event(kind, payload, *, timestamp="2026-01-02T03:04:05+00:00", task_id="task"):
    native = {"item_type": "commandExecution", "thread_id": "thread",
              "turn_id": "turn", "item_id": "item"}
    return {"kind": kind, "timestamp": timestamp, "task_id": task_id,
            "payload": {"native": native, **payload}}


def test_receive_tracks_start_output_finish_and_bounded_fields(monkeypatch):
    reader = Reader()
    index = process_data.ProcessIndex(reader)
    monkeypatch.setattr(process_data, "unpack",
                        lambda value, _reader: (
                            value if "completion_observed" in value
                            else value.get("item", value)
                        ))
    index.receive(event("tool.started", {
        "input": {"item": {"command": "/bin/bash -lc echo", "cwd": "/work",
                           "processId": "pty-1"}},
    }))
    row = next(iter(index.rows.values()))
    assert (row["name"], row["command"], row["cwd"], row["execution_id"]) == (
        "bash", "/bin/bash -lc echo", "/work", "pty-1",
    )
    index.receive(event("codex.item.output", {
        "artifact": {"path": "one", "sha256": "digest"},
    }))
    assert len(row["chunks"]) == 1
    index.receive(event("tool.finished", {
        "result": {"data": {
            "item": {"status": "completed", "exitCode": 0,
                     "command": "ignored", "cwd": "/new"},
            "completion_observed": True,
        }},
    }))
    assert row["state"] == "completed" and row["exit_code"] == 0
    assert row["finished_at"] is not None


def test_receive_ignores_non_commands_and_marks_read_failures(monkeypatch):
    index = process_data.ProcessIndex(Reader())
    index.receive({"kind": "tool.started", "payload": {}})
    index.receive({"kind": "other", "payload": {
        "native": {"item_type": "commandExecution"}}})
    assert index.rows == {}
    monkeypatch.setattr(process_data, "unpack",
                        lambda *_args: (_ for _ in ()).throw(ValueError("bad")))
    index.receive(event("tool.started", {"input": {}}))
    assert next(iter(index.rows.values()))["notice"].startswith("部分进程")


def test_receive_maps_rejected_unknown_and_nonzero_completion(monkeypatch):
    index = process_data.ProcessIndex(Reader())
    monkeypatch.setattr(process_data, "unpack",
                        lambda value, _reader: (
                            value if "completion_observed" in value
                            else value.get("item", value)
                        ))
    index.receive(event("tool.finished", {
        "result": {"data": {"item": {"status": "declined", "exitCode": 1},
                            "completion_observed": True}},
    }))
    assert next(iter(index.rows.values()))["state"] == "declined"

    index = process_data.ProcessIndex(Reader())
    index.receive(event("tool.finished", {
        "result": {"data": {"item": {"status": "completed", "exitCode": 2},
                            "completion_observed": True}},
    }))
    assert next(iter(index.rows.values()))["state"] == "failed"

    index = process_data.ProcessIndex(Reader())
    index.receive(event("tool.finished", {
        "result": {"data": {"item": {"status": "completed", "exitCode": 0},
                            "completion_observed": False}},
    }))
    assert next(iter(index.rows.values()))["state"] == "unknown"


def test_rows_merges_live_children_and_filters_internal_helpers(monkeypatch):
    index = process_data.ProcessIndex(Reader())
    row = {"id": "command_a", "name": "bash", "command": "bash -lc x",
           "cwd": "/work", "state": "running", "started_at": 10,
           "finished_at": None, "chunks": deque(), "input": {}, "result": {},
           "task_id": "", "native": {}, "pid": None}
    index.rows = {"command_a": row}
    owned = [
        {"id": "child", "pid": 123, "ppid": 1, "start": 20, "source": "codex_child",
         "state": "running", "cwd": "/work", "argv": ["bash", "-lc", "bash -lc x"],
         "name": "bash", "parent_id": None, "os_state": "R", "rss": 1024},
        {"id": "helper", "pid": 124, "start": 21, "source": "codex_child",
         "state": "running", "cwd": "/work", "argv": [], "name": "codex"},
    ]
    monkeypatch.setattr(process_data.process_monitor, "snapshot",
                        lambda *_args, **_kwargs: owned)
    rows = index._rows(True)
    command = next(item for item in rows if item["id"] == "command_a")
    assert command["pid"] == 123 and command["state"] == "running"
    assert all(item["id"] != "helper" for item in rows)


def test_page_validates_filters_and_caps_offset(monkeypatch):
    index = process_data.ProcessIndex(Reader())
    monkeypatch.setattr(index, "_rows", lambda _live: [
        {"id": "a", "pid": 1, "name": "bash", "command": "echo", "cwd": "/w",
         "state": "running", "state_label": "运行中", "elapsed": "1秒", "started_at": 3},
        {"id": "b", "pid": 2, "name": "sleep", "command": "sleep", "cwd": "/w",
         "state": "completed", "state_label": "已完成", "elapsed": "2秒", "started_at": 2},
    ])
    result = index.page(True, keyword="BASH", offset=999)
    assert result["total"] == 1 and result["running"] == 1
    assert result["offset"] == 0 and result["rows"][0]["id"] == "a"
    with pytest.raises(ValueError, match="Invalid process query"):
        index.page(True, keyword=1)
    with pytest.raises(ValueError, match="Invalid process query"):
        index.page(True, offset=-1)


def test_output_uses_receipt_then_chunks_and_rejects_bad_artifact():
    native = {"thread_id": "t", "turn_id": "u", "item_id": "i"}
    index = process_data.ProcessIndex(Reader({
        "chunk": json.dumps({"native": native, "text": "tail"}),
    }))
    index.rows["k"] = {
        "chunks": deque([{"path": "chunk", "sha256": "d"}]),
        "native": native, "result": {},
    }
    assert index._output("k") == ("tail", False)
    index.rows["k"]["result"] = {
        "data": {"item": {"aggregatedOutput": "final", "status": "completed"},
                 "completion_observed": True},
    }
    assert index._output("k") == ("final", False)
    index.rows["k"]["chunks"] = deque([{"path": "chunk", "sha256": "d"}])
    index.rows["k"]["result"] = {}
    index.reader.artifacts["chunk"] = json.dumps({"native": {}, "text": "wrong"})
    with pytest.raises(ValueError, match="another command"):
        index._output("k")


def test_detail_is_escaped_bounded_and_surfaces_read_failure(monkeypatch):
    index = process_data.ProcessIndex(Reader())
    monkeypatch.setattr(index, "_rows", lambda _live, detail=None: [
        {"id": "a", "pid": 3, "name": "bash", "state": "running",
         "state_label": "运行中", "elapsed": "1秒", "started_at": 1,
         "finished_at": None, "exit_code": None, "cwd": "/w",
         "command": "echo <x>", "output": "out", "output_truncated": True},
    ])
    detail = index.detail("a", True)
    assert detail["title"].endswith("bash")
    assert detail["script"] == "echo <x>"
    assert "PID" in detail["prefix"] and "运行状态" in detail["prefix"]
    assert detail["output"] == "out" and detail["output_truncated"] is True

    index.rows["a"] = {"chunks": deque(), "native": {}, "result": {}}
    monkeypatch.setattr(index, "_output",
                        lambda _key: (_ for _ in ()).throw(ValueError("bad")))
    detail = index.detail("a", True)
    assert "输出暂不可读" in detail["notice"]
    with pytest.raises(ValueError, match="does not belong"):
        index.detail("missing", True)
