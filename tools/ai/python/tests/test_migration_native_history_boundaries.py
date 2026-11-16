"""Native history admission requires exact assets, origins and home inventory."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sico.migration import native_history
from sico.migration.native_schema import DATABASES

THREAD = "01984de2-8f74-7c91-a3b2-5c5e937cf318"
VERSION = "0.156.1"


def test_validate_assets_matches_exact_profile_and_rejects_modification(monkeypatch):
    monkeypatch.setattr(native_history, "PROFILES", {VERSION: {"skill.md": "sha"}})
    entries = {
        "skills/.system/skill.md": {"kind": "file", "sha256": "sha",
                                    "schema": "native_asset"},
    }
    native_history.validate_assets(entries, VERSION)
    with pytest.raises(ValueError, match="incomplete"):
        native_history.validate_assets(
            {"skills/.system/skill.md": {"kind": "file", "sha256": "other",
                                          "schema": "native_asset"}},
            VERSION)
    with pytest.raises(ValueError, match="Modified"):
        native_history.validate_assets({
            "other": {"kind": "file", "schema": "native_system_asset",
                      "sha256": "bad"},
        }, VERSION)


class Origins:
    def __init__(self):
        self.turns = {}
        self.thread = None

    def observe(self, event):
        if event["kind"] == "codex.thread":
            self.thread = event["payload"]["thread_id"]
        if event["kind"] == "task.started":
            self.turns[(self.thread, event["payload"]["native"]["turn_id"])] = "task"


def test_validate_origins_accepts_one_thread_and_bound_turns(monkeypatch):
    tree = SimpleNamespace(path=Path("/tmp"), open=lambda _path: None)
    root = "/tmp/native"
    inventory = {"/tmp/events.jsonl": {"path": "/tmp/events.jsonl"}}
    monkeypatch.setattr("sico.migration.records.journal",
                        lambda _stream, _session, _inventory: iter((
                            {"kind": "codex.thread", "payload": {"thread_id": THREAD}},
                            {"kind": "task.started", "payload": {
                                "native": {"turn_id": "turn"}}},
                        )))
    @contextmanager
    def checked(_tree, _entry):
        yield object()
    monkeypatch.setattr("sico.migration.sessions.checked_stream", checked)
    monkeypatch.setattr(native_history, "NativeOrigins", Origins)
    native_history.validate_origins(
        tree, root, inventory, {THREAD: {"turns": {"turn": {}}}})


@pytest.mark.parametrize("events,message", [
    ([], "thread set"),
    ([{"kind": "codex.thread", "payload": {"thread_id": THREAD}},
      {"kind": "codex.child", "payload": {}}], "child history"),
])
def test_validate_origins_rejects_missing_thread_or_child(monkeypatch, events, message):
    tree = SimpleNamespace(path=Path("/tmp"), open=lambda _path: None)
    @contextmanager
    def checked(_tree, _entry):
        yield object()
    monkeypatch.setattr("sico.migration.records.journal",
                        lambda *_args: iter(events))
    monkeypatch.setattr("sico.migration.sessions.checked_stream", checked)
    monkeypatch.setattr(native_history, "NativeOrigins", Origins)
    with pytest.raises(ValueError, match=message):
        native_history.validate_origins(
            tree, "/tmp/native", {"/tmp/events.jsonl": {}},
            {THREAD: {"turns": {"turn": {}}}})


def test_validate_origins_requires_owning_journal():
    with pytest.raises(ValueError, match="owning SiCo journal"):
        native_history.validate_origins(
            SimpleNamespace(path=Path("/tmp")), "/tmp/native", {}, {})


def inventory(monkeypatch, tmp_path, *, extra=None):
    root = tmp_path / "native"
    root.mkdir()
    entries = {}
    for name in DATABASES:
        entries["native/" + name] = {"path": str(root / name),
                                     "schema": "native_database",
                                     "action": "archive", "sha256": "sha"}
    entries["native/thread-writer-locks/.coordination.lock"] = {
        "path": str(root / "thread-writer-locks/.coordination.lock"),
        "schema": "native_lock", "action": "lock"}
    relative = "rollout-" + THREAD + ".jsonl"
    entries["native/" + relative] = {
        "path": str(root / relative), "schema": "native_rollout",
        "action": "archive", "sha256": "sha"}
    entries.update(extra or {})
    tree = SimpleNamespace(
        path=tmp_path,
        locks={str(root / "thread-writer-locks/.coordination.lock")},
        open=lambda _path: open("/dev/null", "rb"),
    )
    monkeypatch.setattr(native_history, "snapshot", snapshot)
    monkeypatch.setattr(native_history, "home_version", lambda _connection: VERSION)
    monkeypatch.setattr(native_history, "validate_profile", lambda *_args: None)
    monkeypatch.setattr(native_history, "validate_state",
                        lambda *_args: {THREAD: {
                            "path": str(root / relative), "cwd": "/work"}})
    monkeypatch.setattr(native_history, "validate_assets", lambda *_args: None)
    monkeypatch.setattr(native_history, "validate_rollout",
                        lambda *_args: {"turns": {}, "items": {}, "size": 0,
                                        "next_ordinal": 0})
    monkeypatch.setattr(native_history, "validate_projection", lambda *_args: None)
    monkeypatch.setattr(native_history, "validate_quiescence", lambda *_args: None)
    monkeypatch.setattr(native_history, "validate_origins", lambda *_args: None)
    return tree, root, entries


@contextmanager
def snapshot(_tree, _entry, _wal=None):
    yield object()


def test_validate_home_accepts_fully_inventoried_home(monkeypatch, tmp_path):
    tree, root, entries = inventory(monkeypatch, tmp_path)
    native_history.validate_home(tree, "native", entries)


@pytest.mark.parametrize("damage,message", [
    ("missing", "database or coordination lock is missing"),
    ("lock", "writer lock is not held"),
    ("archive", "not been inventoried"),
])
def test_validate_home_rejects_incomplete_inventory(monkeypatch, tmp_path, damage, message):
    tree, root, entries = inventory(monkeypatch, tmp_path)
    if damage == "missing":
        entries.pop("native/state_5.sqlite")
    elif damage == "lock":
        tree.locks.clear()
    else:
        entries["native/extra"] = {"path": "/tmp/extra", "schema": "native_database",
                                   "action": "archive"}
    with pytest.raises(ValueError, match=message):
        native_history.validate_home(tree, "native", entries)


def test_validate_home_rejects_git_metadata_and_orphan_rollout(monkeypatch, tmp_path):
    tree, root, entries = inventory(monkeypatch, tmp_path)
    entries["native/scratch/HEAD"] = {"path": "/tmp/HEAD",
                                      "schema": "native_git_head",
                                      "action": "keep", "identity": "bad"}
    with pytest.raises(ValueError, match="Git metadata"):
        native_history.validate_home(tree, "native", entries)
    entries.pop("native/scratch/HEAD")
    entries["native/orphan.jsonl"] = {"path": str(root / "orphan.jsonl"),
                                      "schema": "native_rollout",
                                      "action": "archive", "sha256": "sha"}
    with pytest.raises(ValueError, match="orphan"):
        native_history.validate_home(tree, "native", entries)


def test_validate_native_history_isolates_blockers(monkeypatch, tmp_path):
    rows = [
        {"path": "native", "schema": "native_home"},
        {"path": "other", "schema": "other"},
    ]
    monkeypatch.setattr(native_history, "validate_home",
                        lambda *_args: (_ for _ in ()).throw(ValueError("bad")))
    blockers = native_history.validate_native_history(object(), rows)
    assert blockers == [{"path": "native",
                         "code": "invalid_or_unresolved_native_history"}]
    monkeypatch.setattr(native_history, "validate_home", lambda *_args: None)
    assert native_history.validate_native_history(object(), rows) == []
