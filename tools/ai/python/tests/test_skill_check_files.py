from __future__ import annotations

import os

import pytest

from cadai.runtime import MAX_EVAL_BYTES, MAX_SPOOL_BYTES
from cadai.skill_check import MAX_TOKENS, check_source
from cadai.skill_check_tools import check_skill
from cadai.socket_server import RequestFailure


def test_every_skill_path_must_resolve_inside_the_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "outside.il"
    source.write_text("nil")
    with pytest.raises(RequestFailure, match="inside the workspace"):
        check_skill({"path": "../outside.il"}, workspace)
    (workspace / "symlink.il").symlink_to(source)
    with pytest.raises(RequestFailure, match="inside the workspace"):
        check_skill({"path": "symlink.il"}, workspace)
    # Absolute paths outside the workspace are rejected too: the assistant MCP
    # surface never snapshots arbitrary host files (see test_agent_assistant_tools).
    with pytest.raises(RequestFailure, match="inside the workspace"):
        check_skill({"path": str(source)}, workspace)
    inside = workspace / "inside.il"
    inside.write_text("nil")
    assert check_skill({"path": "inside.il"}, workspace)["ok"]


@pytest.mark.parametrize("kind", ["fifo", "directory", "missing", "extension", "size", "encoding"])
def test_invalid_file_sources_are_rejected_without_execution(tmp_path, kind):
    path = tmp_path / ("source.txt" if kind == "extension" else "source.il")
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    elif kind == "size":
        with path.open("wb") as handle:
            handle.truncate(MAX_SPOOL_BYTES + 1)
    elif kind == "encoding":
        path.write_bytes(b"\xff")
    elif kind != "missing":
        path.write_text("nil")
    if kind == "encoding":
        result = check_skill({"path": path.name}, tmp_path)
        assert result["errors"][0]["code"] == "invalid_encoding"
    else:
        with pytest.raises(RequestFailure):
            check_skill({"path": path.name}, tmp_path)


def test_code_size_is_measured_in_utf8_bytes(tmp_path):
    with pytest.raises(RequestFailure, match="UTF-8 bytes"):
        check_skill({"code": "电" * (MAX_EVAL_BYTES // 3 + 1)}, tmp_path)
    with pytest.raises(RequestFailure, match="UTF-8 text"):
        check_skill({"code": "\ud800"}, tmp_path)


def test_large_file_can_be_checked_without_inline_limit(tmp_path):
    path = tmp_path / "large.ils"
    path.write_text(";" + "x" * MAX_EVAL_BYTES + "\nprocedure(foo() t)")
    report = check_skill({"path": path.name}, tmp_path)
    assert report["ok"] and report["source_bytes"] > MAX_EVAL_BYTES


def test_resource_limits_are_explicit_incomplete_results():
    report = check_source(b"x " * (MAX_TOKENS + 1))
    assert not report["ok"]
    assert report["checks"]["lexical"] == "incomplete"
    assert report["checks"]["definition_policy"] == "not_run"
    assert report["errors"][0]["code"] == "scan_limit"
