from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from sico.core.contracts.contracts import BoundContext, RunState
from sico.interpreter import cad_python
from sico.storage.journal.journal import SessionJournal, open_private


def state():
    return RunState(BoundContext("i", "g", "t", {"valid": True}))


def test_single_writer_and_partial_tail(tmp_path):
    with SessionJournal(tmp_path, "s") as journal:
        journal.append("test", {}, state())
        path = journal.directory / "events.jsonl"
        with pytest.raises(RuntimeError, match="active writer"):
            SessionJournal(tmp_path, "s")
    with path.open("ab") as stream:
        stream.write(b'{"contract":')
    with SessionJournal(tmp_path, "s") as reopened:
        assert len(reopened.events()) == 1
        reopened.append("next", {}, state())
    assert len(path.read_text().splitlines()) == 2


def test_complete_corrupt_record_fails_closed(tmp_path):
    with SessionJournal(tmp_path, "s") as journal:
        journal.append("test", {}, state())
        path = journal.directory / "events.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{"contract":"bad"}\n')
    before = path.read_bytes()
    with pytest.raises(ValueError):
        SessionJournal(tmp_path, "s")
    assert path.read_bytes() == before


def test_journal_rows_keep_task_text_out_of_the_state(tmp_path):
    """行内状态不重复携带任务文本：事件流才是完整内容的权威。"""
    journal_state = state()
    journal_state.task = {"id": "task", "status": "executing", "pending_calls": [],
                          "text": "很长的任务文本" * 100}
    with SessionJournal(tmp_path, "s") as journal:
        journal.append("model.delta", {"text": "很"}, journal_state)
        row = journal.records[-1]
        assert "text" not in row["state"]["task"]           # 行内不再复制文本
        assert journal_state.task["text"] == "很长的任务文本" * 100   # 内存状态保留
        assert row["task_id"] == "task" and row["state"]["task"]["status"] == "executing"
    with SessionJournal(tmp_path, "s") as reopened:
        resumed = reopened.state                            # 重开校验通过且无文本
        assert "text" not in resumed.task
        reopened.append("model.delta", {"text": "续"}, resumed)
        assert len(reopened.events()) == 2


def test_alias_preserved_and_artifact_integrity(tmp_path):
    directory = tmp_path / "physical"
    directory.mkdir()
    alias = tmp_path / "NAS alias"
    alias.symlink_to(directory, target_is_directory=True)
    with SessionJournal(alias, "s") as journal:
        assert str(journal.directory).startswith(str(alias))
        data = json.dumps({"text": "中文" * 200}, ensure_ascii=False).encode()
        manifest = journal.artifact(data)
        read = journal.read_artifact(manifest["path"], manifest["sha256"], 0, 40)
        assert read["next_offset"] == 40
        assert "中文" in read["text"]
        with pytest.raises(ValueError):
            journal.read_artifact("../secret", manifest["sha256"])
        with pytest.raises(ValueError):
            journal.read_artifact(manifest["path"], "0" * 64)
        artifact = journal.root / manifest["path"]
        assert artifact.stat().st_mode & 0o777 == 0o600
        artifact.write_bytes(b"tampered")
        with pytest.raises(ValueError):
            journal.read_artifact(manifest["path"], manifest["sha256"])


def test_text_attachment_is_private_utf8_txt_and_checksummed(tmp_path):
    text = "剪贴板内容\n" + "x" * 16001
    with SessionJournal(tmp_path, "text-attachment") as journal:
        manifest = journal.text_attachment(text)
        path = journal.root / manifest["path"]
        assert path.suffix == ".txt"
        assert path.read_text(encoding="utf-8") == text
        assert manifest["complete"] is True
        assert manifest["size"] == len(text.encode("utf-8"))
        assert path.stat().st_mode & 0o777 == 0o600
        assert journal.artifact_text(manifest["path"], manifest["sha256"]) == text


def test_read_artifact_accepts_the_current_session_schematic_export(tmp_path):
    with SessionJournal(tmp_path, "schematic-export") as journal:
        spool = journal.directory / "assistant-spool"
        spool.mkdir(mode=0o700)
        path = spool / ("schematic-export-" + "a" * 24 + ".txt")
        data = b"instance\tname\n"
        path.write_bytes(data)
        os.chmod(path, 0o600)
        digest = hashlib.sha256(data).hexdigest()

        assert journal.artifact_text(str(path), digest) == data.decode()
        assert journal.artifact_text("assistant-spool/" + path.name, digest) == data.decode()

        other = tmp_path / path.name
        other.write_bytes(data)
        os.chmod(other, 0o600)
        with pytest.raises(ValueError, match="query_maestro_results"):
            journal.artifact_text(str(other), digest)


def test_invalid_file_is_not_truncated(tmp_path):
    path = tmp_path / "unsafe"
    path.write_text("preserve")
    path.chmod(0o644)
    with pytest.raises(ValueError):
        open_private(path, os.O_WRONLY | os.O_TRUNC)
    assert path.read_text() == "preserve"
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        open_private(link, os.O_RDONLY)


def test_cad_python_ignores_stale_selectors(tmp_path):
    explicit = tmp_path / "selected Python"
    explicit.symlink_to("/software/pkgs/python/3.9.13/bin/python3")
    expected = Path(__file__).resolve().parents[4] / "runtime/python/bin/python3"
    assert cad_python({
        "SICO_PYTHON": str(explicit),
        "SICO_PYTHON_ROOT": "/missing",
        "CAD_PYTHON": str(explicit),
    }) == str(expected)
