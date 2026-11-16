from __future__ import annotations

import json
import os
import shutil
import sys
import time

import pytest

from assistant_installation_fixture import assistant_installation

from cadai.mcp import McpServer
from cadai.search import MAX_RESULT_BYTES, search
from cadai.search_process import MAX_STREAM_BYTES, run_search_process
from cadai.search_runtime import RG_RUNTIME_RELATIVE, prepare_search_environment, resolve_rg
from cadai.search_tools import SEARCH_TOOL
from cadai.socket_server import RequestFailure


def test_literal_regex_case_globs_and_content_locations(tmp_path):
    (tmp_path / "source.il").write_text("before\nprocedure(foo()\nAFTER\n")
    (tmp_path / "other.txt").write_text("procedure(other()\n")
    result = search({"pattern": "procedure(", "globs": ["*.il"]}, tmp_path)
    assert result["complete"] and result["returned"] == 1
    assert result["results"][0]["line_number"] == 2
    assert result["results"][0]["byte_column"] == 1
    assert result["results"][0]["text"] == "procedure(foo()"
    assert result["results"][0]["path"] == "./source.il"
    regex = search(
        {"pattern": "^after$", "fixed_strings": False, "case_sensitive": False}, tmp_path
    )
    assert regex["results"][0]["line_number"] == 3
    assert search({"pattern": "after"}, tmp_path)["returned"] == 0


def test_files_mode_handles_unicode_spaces_colons_and_newlines(tmp_path):
    for name in ("器件 source.il", "a:b.il", "new\nline.il", "skip.txt"):
        (tmp_path / name).touch()
    result = search({"mode": "files", "globs": ["*.il"]}, tmp_path)
    assert result["complete"]
    assert {item["path"] for item in result["results"]} == {
        "./器件 source.il",
        "./a:b.il",
        "./new\nline.il",
    }


def test_ignores_hidden_binary_large_files_and_symlinks(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_text("needle")
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    (tmp_path / "ignored.txt").write_text("needle")
    (tmp_path / ".hidden").write_text("needle")
    (tmp_path / "source.txt").write_text("needle")
    (tmp_path / "binary").write_bytes(b"\x00needle\x00")
    (tmp_path / "large").write_bytes(b"needle" + b"x" * (8 * 1024 * 1024))
    (tmp_path / "link").symlink_to(tmp_path / "source.txt")
    result = search({"pattern": "needle"}, tmp_path)
    assert {item["path"] for item in result["results"]} == {"./source.txt"}
    result = search(
        {"pattern": "needle", "include_hidden": True, "respect_ignore": False}, tmp_path
    )
    assert {item["path"] for item in result["results"]} == {
        "./source.txt",
        "./ignored.txt",
        "./.hidden",
    }


def test_explicit_path_and_traversal_rules(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("needle")
    assert search({"pattern": "needle", "path": str(outside)}, workspace)["returned"] == 1
    for path in ("../outside", str(workspace / "link"), str(workspace / "fifo")):
        if path.endswith("link"):
            (workspace / "link").symlink_to(outside)
        if path.endswith("fifo"):
            os.mkfifo(path)
        with pytest.raises(RequestFailure, match="escapes|symlink|regular"):
            search({"pattern": "needle", "path": path}, workspace)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"pattern": ""},
        {"pattern": "a\nb"},
        {"pattern": "\x00"},
        {"pattern": "\ud800"},
        {"pattern": "x", "path": ""},
        {"pattern": "x", "shell": True},
        {"pattern": "x", "max_results": True},
        {"pattern": "x", "max_results": 1001},
        {"pattern": "x", "timeout_seconds": 0},
        {"pattern": "x", "timeout_seconds": float("nan")},
        {"pattern": "x", "case_sensitive": "false"},
        {"pattern": "x", "globs": "*.il"},
        {"pattern": "x", "globs": ["x"] * 21},
        {"pattern": "x", "mode": []},
        {"mode": "files", "pattern": "x"},
        {"mode": "files", "fixed_strings": True},
    ],
)
def test_reject_invalid_arguments(tmp_path, arguments):
    with pytest.raises(RequestFailure) as error:
        search(arguments, tmp_path)
    assert error.value.code == "invalid_params"


def test_no_match_and_invalid_regex_are_distinct(tmp_path):
    (tmp_path / "source").write_text("needle")
    absent = search({"pattern": "absent"}, tmp_path)
    assert absent["ok"] and absent["complete"] and absent["results"] == []
    invalid = search({"pattern": "[", "fixed_strings": False}, tmp_path)
    assert not invalid["ok"] and invalid["code"] == "search_failed"
    assert "unclosed character class" in invalid["diagnostic"]


def test_no_shell_or_user_rg_config_or_path_dependency(tmp_path, monkeypatch):
    marker = tmp_path / "EXECUTED"
    config = tmp_path / "evil-config"
    config.write_text("--pre=touch " + str(marker))
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", str(config))
    monkeypatch.setenv("PATH", "/no-system-rg")
    pattern = "--pre=$(touch EXECUTED);`touch EXECUTED`"
    (tmp_path / "--pre=bad").write_text(pattern)
    result = search({"pattern": pattern, "path": "./--pre=bad"}, tmp_path)
    assert result["returned"] == 1 and result["ok"]
    assert not marker.exists()


def test_result_limits_mark_incomplete_and_exact_limit_is_complete(tmp_path):
    (tmp_path / "source").write_text("needle\n" * 3)
    assert search({"pattern": "needle", "max_results": 3}, tmp_path)["complete"]
    result = search({"pattern": "needle", "max_results": 2}, tmp_path)
    assert result["truncated"] and result["truncation_reason"] == "max_results"
    assert result["returned"] == 2 and not result["complete"]


def test_output_bytes_and_long_records_are_bounded(tmp_path):
    file = tmp_path / "source"
    file.write_text(("needle" + "界" * 1800 + "\n") * 50)
    result = search({"pattern": "needle", "max_results": 1000}, tmp_path)
    assert result["truncated"] and result["returned"] < 50
    assert len(json.dumps(result, ensure_ascii=True).encode()) <= MAX_RESULT_BYTES
    assert result["results"][0]["text_truncated"]
    file.write_text("needle" + "x" * 400_000)
    result = search({"pattern": "needle"}, tmp_path)
    assert result["truncation_reason"] == "record_limit" and not result["complete"]


def test_invalid_utf8_is_represented_without_crashing(tmp_path):
    filename = os.fsencode(tmp_path) + b"/invalid-\xff"
    with open(filename, "wb") as handle:
        handle.write(b"needle \xff\n")
    result = search({"pattern": "needle"}, tmp_path)["results"][0]
    assert result["text_encoding_replaced"] and result["path_bytes_base64"]
    assert search({"mode": "files"}, tmp_path)["results"][0]["path_bytes_base64"]


def test_process_timeout_kills_and_reaps_child(tmp_path):
    pid_file = tmp_path / "pid"
    code = "import os,time; open('pid','w').write(str(os.getpid())); time.sleep(60)"
    start = time.monotonic()
    status, stderr, reason = run_search_process(
        [sys.executable, "-c", code], tmp_path, 1, lambda _: None
    )
    assert reason == "timeout" and status < 0 and time.monotonic() - start < 5
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_stderr_is_drained_but_bounded(tmp_path):
    code = f'import os; os.write(2, b"x" * {MAX_STREAM_BYTES + 9000})'
    _status, stderr, reason = run_search_process(
        [sys.executable, "-c", code], tmp_path, 2, lambda _: None
    )
    assert len(stderr) == 4096 and reason == "stream_limit"


def test_mcp_search_is_local(tmp_path):
    class Client:
        def call(self, *_args):
            pytest.fail("Search must not contact Virtuoso")

    (tmp_path / "source").write_text("needle")
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "Search", "arguments": {"pattern": "needle"}},
    }
    result = McpServer(Client(), workspace=tmp_path).handle(request)["result"]
    assert not result["isError"]
    assert json.loads(result["content"][0]["text"])["returned"] == 1
    request["params"]["arguments"] = {"pattern": "[", "fixed_strings": False}
    assert McpServer(Client(), workspace=tmp_path).handle(request)["result"]["isError"]
    assert SEARCH_TOOL["annotations"]["readOnlyHint"]


def test_runtime_and_terminal_path_prioritize_bundle(tmp_path, monkeypatch):
    source = resolve_rg({})
    root = assistant_installation(tmp_path / "product", monkeypatch)
    installed = root / "tools/ai" / RG_RUNTIME_RELATIVE
    installed.parent.mkdir(parents=True)
    shutil.copy2(source, installed)
    environment = {"PATH": "/unavailable", "SICO_HOME": str(root),
                   "CAD_AI_RELEASE_ROOT": str(root / "tools/ai")}
    assert resolve_rg(environment) == installed
    prepare_search_environment(environment)
    prepare_search_environment(environment)
    assert environment["PATH"].split(os.pathsep) == [str(installed.parent), "/unavailable"]
    assert shutil.which("rg", path=environment["PATH"]) == str(installed)


def test_missing_bundle_is_actionable_and_does_not_use_system_rg(tmp_path, monkeypatch):
    assistant_installation(tmp_path / "empty-product", monkeypatch)
    with pytest.raises(FileNotFoundError, match="Bundled ripgrep"):
        resolve_rg({"PATH": os.defpath})
