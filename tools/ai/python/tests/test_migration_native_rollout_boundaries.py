"""Native rollout envelopes, item receipts and turn completion are strict."""

from __future__ import annotations

import hashlib
import json
import os

import pytest
from sico.migration import native_rollout
from sico.migration.filesystem import fingerprint

THREAD = "01984de2-8f74-7c91-a3b2-5c5e937cf318"
CWD = "/native/home"
VERSION = "0.162.1"


def rows():
    return [
        {"ordinal": 0, "type": "session_meta", "payload": {
            "id": THREAD, "session_id": THREAD, "cwd": CWD,
            "cli_version": VERSION, "history_mode": "paginated"}},
        {"ordinal": 1, "type": "event_msg", "payload": {
            "type": "task_started", "turn_id": "turn"}},
        {"ordinal": 2, "type": "response_item", "payload": {
            "type": "function_call", "call_id": "call"}},
        {"ordinal": 3, "type": "event_msg", "payload": {
            "type": "item_completed", "turn_id": "turn", "thread_id": THREAD,
            "item": {"type": "McpToolCall", "id": "call", "status": "completed"}}},
        {"ordinal": 4, "type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "call"}},
        {"ordinal": 5, "type": "event_msg", "payload": {
            "type": "item_completed", "turn_id": "turn", "thread_id": THREAD,
            "item": {"type": "AgentMessage", "id": "message"}}},
        {"ordinal": 6, "type": "event_msg", "payload": {
            "type": "task_complete", "turn_id": "turn"}},
    ]


def install(monkeypatch, values):
    monkeypatch.setattr(
        native_rollout, "lines",
        lambda _tree, _entry: (
            (index, index + 1, row) for index, row in enumerate(values)
        ),
    )


@pytest.mark.parametrize("item,kind", [
    ({"type": "AgentMessage", "id": "a"}, "agentMessage"),
    ({"type": "McpToolCall", "id": "a", "status": "completed"}, "mcpToolCall"),
    ({"type": "CommandExecution", "id": "a", "status": "failed",
      "exitCode": 1}, "commandExecution"),
])
def test_item_status_accepts_known_terminal_items(item, kind):
    assert native_rollout.item_status(item) == kind


@pytest.mark.parametrize("item,message", [
    ({"type": "Unknown", "id": "a"}, "type or identity"),
    ({"type": "AgentMessage"}, "type or identity"),
    ({"type": "McpToolCall", "id": "a", "status": "running"}, "terminal receipt"),
    ({"type": "CommandExecution", "id": "a", "status": "completed"}, "exit receipt"),
])
def test_item_status_rejects_unqualified_items(item, message):
    with pytest.raises(ValueError, match=message):
        native_rollout.item_status(item)


def test_validate_rollout_accepts_completed_turn_with_terminal_call(monkeypatch):
    install(monkeypatch, rows())
    result = native_rollout.validate_rollout(
        object(), {"path": "rollout"}, THREAD, CWD, VERSION)
    assert set(result["turns"]) == {"turn"}
    assert ("turn", "call") in result["items"]
    assert result["next_ordinal"] == 7


@pytest.mark.parametrize("damage,message", [
    (lambda value: value.__setitem__(1, dict(value[1], ordinal=5)), "envelope"),
    (lambda value: value[1]["payload"].update(type="unknown"), "Unqualified native event"),
    (lambda value: value.__setitem__(1, dict(value[1], type="session_meta")), "repeats"),
    (lambda value: value[2]["payload"].update(type="function_call", call_id=None),
     "active turn or identity"),
    (lambda value: value[2]["payload"].update(type="custom"), "Unqualified native response"),
    (lambda value: value[3]["payload"].update(thread_id="other"), "another turn"),
    (lambda value: value[3]["payload"]["item"].update(status="running"),
     "terminal receipt"),
    (lambda value: value.__setitem__(6, {"ordinal": 6, "type": "event_msg",
                                         "payload": {"type": "token_count"}}),
     "incomplete turn"),
])
def test_validate_rollout_rejects_unqualified_evidence(monkeypatch, damage, message):
    value = rows()
    damage(value)
    install(monkeypatch, value)
    with pytest.raises(ValueError, match=message):
        native_rollout.validate_rollout(
            object(), {"path": "rollout"}, THREAD, CWD, VERSION)


def test_validate_rollout_rejects_duplicate_and_unmatched_calls(monkeypatch):
    value = rows()
    value[2]["payload"] = {"type": "function_call", "call_id": "call"}
    value.insert(3, {"ordinal": 3, "type": "response_item", "payload": {
        "type": "function_call", "call_id": "call"}})
    for index, row in enumerate(value):
        row["ordinal"] = index
    install(monkeypatch, value)
    with pytest.raises(ValueError, match="identity overlaps"):
        native_rollout.validate_rollout(
            object(), {"path": "rollout"}, THREAD, CWD, VERSION)

    value = rows()
    value[4]["payload"]["call_id"] = "other"
    install(monkeypatch, value)
    with pytest.raises(ValueError, match="matching call"):
        native_rollout.validate_rollout(
            object(), {"path": "rollout"}, THREAD, CWD, VERSION)


def test_validate_rollout_rejects_missing_terminal_operation(monkeypatch):
    value = rows()
    value[3]["payload"]["item"]["id"] = "different"
    install(monkeypatch, value)
    with pytest.raises(ValueError, match="qualified terminal"):
        native_rollout.validate_rollout(
            object(), {"path": "rollout"}, THREAD, CWD, VERSION)


def test_lines_checks_identity_hashes_and_truncation(tmp_path):
    path = tmp_path / "rollout.jsonl"
    payload = (json.dumps(rows()[0]) + "\n").encode()
    path.write_bytes(payload)
    info = fingerprint(os.stat(path))
    entry = {"path": str(path), "identity": info,
             "sha256": hashlib.sha256(payload).hexdigest()}

    class Tree:
        def open(self, _path):
            return path.open("rb")

    assert [row[2]["ordinal"] for row in native_rollout.lines(Tree(), entry)] == [0]
    with pytest.raises(ValueError, match="changed before"):
        native_rollout.lines(Tree(), {**entry, "identity": dict(info, size=0)}).__next__()
    path.write_bytes(b"{}\n")
    changed = fingerprint(os.stat(path))
    with pytest.raises(ValueError, match="during validation"):
        list(native_rollout.lines(Tree(), {**entry, "identity": changed}))


def test_validate_index_requires_exact_identity(monkeypatch):
    install(monkeypatch, [
        {"id": THREAD, "thread_name": "name", "updated_at": "now"},
    ])
    native_rollout.validate_index(object(), {"path": "index"}, {THREAD})
    with pytest.raises(ValueError, match="identity mismatch"):
        native_rollout.validate_index(object(), {"path": "index"}, {"other"})
