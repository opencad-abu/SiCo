from __future__ import annotations

import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cadai.mcp import McpServer
from cadai.socket_server import RequestFailure
from cadai.tool_help import cds_help
from cadai.tool_help_index_data import ROWS, SOURCE_SHA256
from cadai.tool_help_schema import DOC_ENV_NAMES

AI_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("PATH", "/no/cadence/tools")
    for name in DOC_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_preset_preserves_all_user_rows_and_is_reproducible():
    source = AI_ROOT / "docs/tool-help/IC_doc_index.md"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == SOURCE_SHA256
    assert len(ROWS) == len({row[0] for row in ROWS}) == 248
    generator = runpy.run_path(str(source.with_name("build_index.py")))
    assert (
        generator["generate"](source)
        == (AI_ROOT / "python/cadai/tool_help_index_data.py").read_text()
    )
    mapping = cds_help({"action": "index", "kind": "help_mapping", "max_results": 100})
    assert mapping["total_matches"] == 43


def test_offline_query_returns_manual_scope_and_original_description():
    result = cds_help({"action": "index", "query": "cdfuser"})
    first = result["entries"][0]
    assert first["manual"] == "cdfuser" and first["kind"] == "manual"
    assert "Component Description Format User Guide" in first["description"]
    assert first["directory_exists"] is None and result["source"] is None
    assert result["index"]["version"] == "IC23.10.130"
    assert result["index"]["sha256"] == SOURCE_SHA256
    assert (
        cds_help({"action": "index", "query": "ADE SKILL"})["entries"][0]["manual"]
        == "maeSKILLref"
    )


def test_mapping_does_not_masquerade_as_manual_or_claim_exhaustive_targets():
    result = cds_help({"action": "index", "query": "maestro"})
    entry = result["entries"][0]
    assert entry["manual"] == "maestro" and entry["kind"] == "help_mapping"
    assert {value["manual"] for value in entry["listed_targets"]} == {
        "assembler",
        "Explorer",
        "spectreRFinExplorer",
    }
    assert entry["targets_exhaustive"] is False


def test_pagination_covers_every_directory_with_bounded_output():
    offset = 0
    collected = []
    while True:
        result = cds_help({"action": "index", "offset": offset, "max_results": 100})
        assert len(json.dumps(result, ensure_ascii=True).encode()) <= 65536
        collected.extend(entry["manual"] for entry in result["entries"])
        if result["next_offset"] is None:
            assert result["complete"]
            break
        assert result["truncated"] and result["next_offset"] > offset
        offset = result["next_offset"]
    assert len(collected) == len(set(collected)) == 248


def test_installed_version_checked_but_index_version_not_rewritten(tmp_path):
    root = tmp_path / "IC_DIFFERENT_VERSION"
    (root / "doc/cdfuser").mkdir(parents=True)
    (root / "doc/cdfuser/index.html").write_text("<p>parameters actual version</p>")
    result = cds_help({"action": "index", "install_root": str(root), "query": "cdfuser"})
    assert result["entries"][0]["directory_exists"] is True
    assert result["index"]["version"] == "IC23.10.130"
    assert result["source"]["installation"] == "IC_DIFFERENT_VERSION"
    missing = cds_help({"action": "index", "install_root": str(root), "query": "sklangref"})
    assert missing["entries"][0]["directory_exists"] is False
    found = cds_help(
        {
            "action": "search",
            "install_root": str(root),
            "manual": result["entries"][0]["manual"],
            "query": "parameters",
        }
    )
    assert found["results"][0]["page"] == "cdfuser/index.html"


def test_directory_check_rejects_symlink_escape(tmp_path):
    root = tmp_path / "IC"
    (root / "doc").mkdir(parents=True)
    (root / "doc/cdfuser").symlink_to(tmp_path)
    result = cds_help({"action": "index", "install_root": str(root), "query": "cdfuser"})
    assert result["entries"][0]["directory_exists"] is False


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "index", "kind": []},
        {"action": "index", "offset": True},
        {"action": "index", "query": ""},
        {"action": "index", "timeout_seconds": 1},
        {"action": "index", "manual": "cdfuser"},
        {"action": "index", "offset": 249},
        {"action": "index", "tool": "spectre"},
        {"action": "search", "query": "x", "kind": "manual"},
    ],
)
def test_invalid_index_requests(arguments):
    with pytest.raises(RequestFailure):
        cds_help(arguments)


def test_no_match_and_support_filter():
    empty = cds_help({"action": "index", "query": "not-present-987"})
    assert empty["entries"] == [] and empty["complete"]
    support = cds_help({"action": "index", "kind": "support"})
    assert support["total_matches"] == 12
    assert all(entry["kind"] == "support" for entry in support["entries"])


def test_mcp_index_local_and_available_without_cadence():
    class Client:
        def call(self, *args):
            pytest.fail("index must not call Virtuoso")

    response = McpServer(Client()).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "cds_help", "arguments": {"action": "index", "query": "cdfuser"}},
        }
    )
    assert not response["result"]["isError"]
    assert json.loads(response["result"]["content"][0]["text"])["entries"][0]["manual"] == "cdfuser"


def test_isolated_source_dependencies_contain_help_preset(tmp_path):
    shutil.copytree(
        AI_ROOT / "python/cadai",
        tmp_path / "cadai",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    # Source-test isolation includes the declared common Python dependencies.
    # This is not a runtime distribution package or release acceptance.
    common = AI_ROOT.parent / "common/python"
    # No Downloads, installed Cadence or docs directory required for the lookup.
    env = {
        "PATH": "/no/tools",
        "PYTHONPATH": str(common),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH", ""),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from cadai.tool_help import cds_help; "
            'r=cds_help({"action":"index","query":"cdfuser"}); '
            'assert r["entries"][0]["manual"] == "cdfuser"; print(r["index"]["entries"])',
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "248"
