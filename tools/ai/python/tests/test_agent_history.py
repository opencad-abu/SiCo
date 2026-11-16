"""History reads remain passive while another process owns an active journal."""

from __future__ import annotations

import json
import os

import pytest
from sico.core.contracts import BoundContext, RunState
from sico.storage.history import SessionReader, session_catalog
from sico.storage.journal import SessionJournal


def test_read_only_history_preserves_writer_partial_tail_and_private_blocks(tmp_path):
    with SessionJournal(tmp_path, "history") as journal:
        state = RunState(BoundContext("i", "g", "bound", {}))
        state.messages = [
            {
                "role": "assistant",
                "content": "",
                "provider_blocks": [{"type": "thinking", "thinking": "private reasoning"}],
            }
        ]
        journal.append("session.created", {}, state)
        path = journal.directory / "events.jsonl"
        with path.open("ab") as stream:
            stream.write(b'{"partial":')
        before = path.read_bytes()
        reader = SessionReader(journal.root, journal.session_id)
        events = reader.events()
        assert len(events) == 1
        assert "state" not in events[0]
        assert "private reasoning" not in json.dumps(events)
        assert path.read_bytes() == before
        assert session_catalog(journal.root)[0]["id"] == "history"
        with pytest.raises(RuntimeError, match="active writer"):
            SessionJournal(tmp_path, "history")


def test_a_long_journal_lists_and_reads_without_a_size_cap(tmp_path):
    """History is never truncated or refused: only rendering keeps the newest."""
    from sico.storage.history import CatalogIndex

    with SessionJournal(tmp_path, "long") as journal:
        state = RunState(BoundContext("i", "g", "bound", {}))
        for _index in range(300):
            journal.append("session.created", {"padding": "x" * 400}, state)
        reader = SessionReader(journal.root, journal.session_id)
        assert len(reader.events()) == 300
        # Streaming gives the same records without materializing them.
        assert sum(1 for _event in reader.stream_events()) == 300
        rows = CatalogIndex(journal.root).scan()
        assert rows[journal.session_id].get("unavailable") is None
        assert session_catalog(journal.root)[0]["id"] == "long"


def test_history_rejects_unsafe_files_wrong_identity_and_foreign_artifacts(tmp_path):
    with SessionJournal(tmp_path, "first") as first, SessionJournal(tmp_path, "second") as second:
        state = RunState(BoundContext("i", "g", "bound", {}))
        first.append("session.created", {}, state)
        first_data = first.artifact(b"{}")
        second_data = second.artifact(b"{}")
        reader = SessionReader(first.root, "first")
        assert reader.artifact_text(first_data["path"], first_data["sha256"]) == "{}"
        with pytest.raises(ValueError):
            reader.artifact_text(second_data["path"], second_data["sha256"])
        (first.root / "sessions" / "link").symlink_to(first.directory)
        with pytest.raises(ValueError):
            SessionReader(first.root, "link")
        assert "link" not in [r["id"] for r in session_catalog(first.root)]
        row = first.events()[0]
        row["session_id"] = "wrong"
        path = second.directory / "events.jsonl"
        path.write_text(json.dumps(row) + "\n")
        with pytest.raises(ValueError, match="身份"):
            SessionReader(second.root, "second").events()
        row.update(
            session_id="second", kind="tool.finished", payload={"id": "t", "name": "get_context"}
        )
        path.write_text(json.dumps(row) + "\n")
        with pytest.raises(ValueError, match="tool result"):
            SessionReader(second.root, "second").events()
        path.unlink()
        os.mkfifo(path, 0o600)
        with pytest.raises(ValueError, match="regular"):
            SessionReader(second.root, "second").events()


def test_catalog_recovers_when_a_resumed_read_races_a_rewrite(tmp_path):
    """A cached offset must never turn a readable journal into an unavailable row."""
    from sico.storage.history import CatalogIndex

    with SessionJournal(tmp_path, "raced") as journal:
        state = RunState(BoundContext("i", "g", "bound", {}))
        for _index in range(5):
            journal.append("session.created", {"padding": ""}, state)
        root = journal.root
        path = journal.directory / "events.jsonl"

    index = CatalogIndex(root)
    assert index.scan()["raced"].get("unavailable") is None

    # Rewrite the file in place and longer: the cached resume offset now lands
    # inside a record, which used to raise and mark the session unavailable.
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["payload"]["padding"] = "x" * 4096
    lines[0] = json.dumps(first)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rows = index.scan()
    assert rows["raced"].get("unavailable") is None
    assert rows["raced"]["id"] == "raced"


def test_catalog_retry_does_not_hide_corruption_and_recovers_after_repair(tmp_path, monkeypatch):
    from sico.storage.history import CatalogIndex

    with SessionJournal(tmp_path, "raced") as journal:
        journal.append("session.created", {}, RunState(BoundContext("i", "g", "bound", {})))
        path, root = journal.directory / "events.jsonl", journal.root
    good = path.read_bytes()
    index = CatalogIndex(root)
    index.scan()
    attempts = []
    read = index._read_once

    def counted(session_id, cached):
        attempts.append(cached is not None)
        return read(session_id, cached)

    monkeypatch.setattr(index, "_read_once", counted)
    path.write_bytes(good + b'{"sequence":2}\n')
    row = index.scan()["raced"]
    assert row["damaged"] and row["unavailable_detail"]
    assert attempts == [True, False]
    assert path.read_bytes() == good + b'{"sequence":2}\n'
    path.write_bytes(good)
    assert not index.scan()["raced"].get("unavailable")


def test_catalog_resumes_a_partial_tail_without_marking_it_unavailable(tmp_path):
    from sico.storage.history import CatalogIndex

    with SessionJournal(tmp_path, "tail") as journal:
        state = RunState(BoundContext("i", "g", "bound", {}))
        journal.append("session.created", {}, state)
        root, path = journal.root, journal.directory / "events.jsonl"
    original = path.read_bytes()
    next_row = json.loads(original)
    next_row.update(sequence=2, kind="codex.thread.name", payload={"name": "恢复后的名称"})
    data = (json.dumps(next_row) + "\n").encode()
    index = CatalogIndex(root)
    index.scan()
    with path.open("ab") as stream:
        stream.write(data[:20])
    assert not index.scan()["tail"].get("unavailable")
    assert path.read_bytes() == original + data[:20]
    with path.open("ab") as stream:
        stream.write(data[20:])
    assert index.scan()["tail"]["name"] == "恢复后的名称"
