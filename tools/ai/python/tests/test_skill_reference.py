from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from cadai.mcp import McpServer
from cadai.skill_reference import (
    MAX_DOCUMENT_CHARS,
    MAX_EXACT_MATCHES,
    MAX_QUERY_CHARS,
    SkillReference,
    SkillReferenceArgumentError,
    SkillReferenceUnavailable,
    default_reference_root,
)
from cadai.skill_reference_tools import MAX_SEARCH_RESULTS


def test_search_skill_api_returns_ranked_bounded_matches(reference_root: Path) -> None:
    reference = SkillReference(reference_root)

    result = reference.search({"query": "substring", "limit": 2})

    assert result["query"] == "substring"
    assert result["total_matches"] == 1
    assert result["matches"] == [
        {
            "name": "substring",
            "reference_id": "skill-language/substring",
            "usage": "substring( S_string x_index [ x_length ] )",
            "summary": "Returns a substring beginning at x_index.",
            "doc_set": "SKILL Language User Guide",
            "document": "sklangref/substring.html",
            "product_version": "ICADVM 23.1",
        }
    ]
    assert result["reference"] == {
        "function_count": 3,
        "document_count": 3,
        "copyrights": ["Copyright 2025 Cadence Design Systems, Inc."],
    }
    reference.close()


@pytest.mark.parametrize("name", ["SUBSTRING", "skill-language/substring"])
def test_get_skill_api_requires_an_exact_name_or_reference_id(
    reference_root: Path, name: str
) -> None:
    reference = SkillReference(reference_root)

    result = reference.get({"name": name})

    assert result["query"] == name
    assert [match["name"] for match in result["matches"]] == ["substring"]
    assert result["matches"][0]["usage"] == "substring( S_string x_index [ x_length ] )"
    assert "x_length is optional" in result["matches"][0]["documentation"]
    assert result["matches"][0]["truncated"] is False
    assert result["total_matches"] == 1
    assert result["truncated"] is False
    reference.close()


def test_get_skill_api_returns_all_bundled_entries_for_a_duplicate_name() -> None:
    reference = SkillReference()

    result = reference.get({"name": "defun"})

    assert result["total_matches"] == 6
    assert result["truncated"] is False
    assert len(result["matches"]) == 6
    assert result["matches"][0]["reference_id"] == "funcprog_re_defun"
    reference.close()


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("search_skill_api", {}, "query must be a string"),
        ("search_skill_api", {"query": "   "}, "1 to 128 characters"),
        ("search_skill_api", {"query": "x", "limit": True}, "limit must be an integer"),
        ("search_skill_api", {"query": "x", "extra": 1}, "unexpected arguments"),
        ("get_skill_api", {"name": "notAnExactApi"}, "no exact SKILL API match"),
        ("get_skill_api", {"name": "x", "max_chars": "x"}, "max_chars must be an integer"),
        ("get_skill_api", {"name": "x" * (MAX_QUERY_CHARS + 1)}, "1 to 128 characters"),
        ("unknown", {}, "unknown SKILL reference tool"),
    ],
)
def test_skill_reference_rejects_invalid_arguments(
    reference_root: Path, tool: str, arguments: dict[str, Any], message: str
) -> None:
    reference = SkillReference(reference_root)

    with pytest.raises(SkillReferenceArgumentError, match=message):
        reference.call(tool, arguments)


def test_skill_reference_clamps_oversized_budgets(reference_root: Path) -> None:
    reference = SkillReference(reference_root)

    search = reference.search({"query": "set", "limit": MAX_SEARCH_RESULTS + 20})
    assert len(search["matches"]) <= MAX_SEARCH_RESULTS

    document = reference.get({"name": "substring", "max_chars": MAX_DOCUMENT_CHARS + 1})
    assert all(
        len(row["documentation"]) <= MAX_DOCUMENT_CHARS for row in document["matches"]
    )
    reference.close()

    reference.close()


def test_skill_reference_reports_a_missing_database(tmp_path: Path) -> None:
    reference = SkillReference(tmp_path / "missing-reference")

    with pytest.raises(SkillReferenceUnavailable, match="database is unavailable") as error:
        reference.search({"query": "substring"})
    assert "SICO_AI_SKILL_REFERENCE_ROOT" in str(error.value)
    assert "cds_help" in str(error.value)


def test_reference_store_serves_calls_from_other_threads(reference_root: Path) -> None:
    """工具桥按请求分配线程：连接打开后必须能被其它线程继续使用。"""
    reference = SkillReference(reference_root)
    assert reference.search({"query": "substring"})["total_matches"] == 1
    barrier, outcomes = threading.Barrier(3), {}

    def call(tag: str) -> None:
        barrier.wait()
        try:
            outcomes[tag] = len(reference.search({"query": "substring"})["matches"])
        except Exception as exc:  # 记录失败原因，供断言展示
            outcomes[tag] = exc

    threads = [threading.Thread(target=call, args=(tag,)) for tag in ("t1", "t2")]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert outcomes == {"t1": 1, "t2": 1}
    assert reference.search({"query": "substring"})["total_matches"] == 1
    reference.close()


def test_reference_store_reopens_after_a_broken_connection(reference_root: Path) -> None:
    reference = SkillReference(reference_root)
    assert reference.search({"query": "substring"})["total_matches"] == 1
    reference._store._connection.close()            # 模拟会话中途连接被破坏
    assert reference.search({"query": "substring"})["total_matches"] == 1
    reference.close()


def test_skill_reference_rejects_an_invalid_schema(tmp_path: Path) -> None:
    root = tmp_path / "invalid-reference"
    root.mkdir()
    with sqlite3.connect(root / "skill_api.sqlite3") as connection:
        connection.execute("PRAGMA user_version = 1")

    reference = SkillReference(root)

    with pytest.raises(SkillReferenceUnavailable, match="functions table is missing"):
        reference.search({"query": "substring"})


def test_skill_reference_rejects_invalid_metadata(reference_root: Path) -> None:
    with sqlite3.connect(reference_root / "skill_api.sqlite3") as connection:
        connection.execute("UPDATE metadata SET value = '{}' WHERE key = 'copyrights'")

    reference = SkillReference(reference_root)

    with pytest.raises(SkillReferenceUnavailable, match="copyrights metadata"):
        reference.search({"query": "substring"})


def test_get_skill_api_marks_truncated_documentation(reference_root: Path) -> None:
    reference = SkillReference(reference_root)

    match = reference.get({"name": "longDocument", "max_chars": 1_024})["matches"][0]

    assert len(match["documentation"]) == 1_024
    assert match["truncated"] is True
    reference.close()


def test_reference_root_honors_environment_override(tmp_path: Path, monkeypatch) -> None:
    configured = tmp_path / "configured-reference"
    monkeypatch.setenv("SICO_AI_SKILL_REFERENCE_ROOT", f"  {configured}  ")

    assert default_reference_root() == configured


def test_reference_root_resolves_relative_override_in_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SICO_AI_SKILL_REFERENCE_ROOT", "relative/reference")
    monkeypatch.setenv("SICO_AI_WORKSPACE", str(tmp_path))
    assert default_reference_root() == tmp_path / "relative/reference"


def test_bundled_reference_is_complete_and_legacy_sqlite_compatible() -> None:
    reference = SkillReference()

    for name in (
        "substring",
        "lmgrDefineInits",
        "gdmstatus",
        "dbOpenCellViewByType",
        "hiCreateAppForm",
        "rodCreatePath",
    ):
        assert reference.get({"name": name})["matches"][0]["name"] == name

    metadata = reference.search({"query": "substring", "limit": 1})["reference"]
    assert metadata["function_count"] >= 8_000
    assert metadata["document_count"] >= 8_000
    path_creators = reference.search({"query": "create path", "limit": 5})["matches"]
    assert "rodCreatePath" in {match["name"] for match in path_creators}
    application_forms = reference.search({"query": "application form", "limit": 5})[
        "matches"
    ]
    assert "hiCreateAppForm" in {match["name"] for match in application_forms}
    with sqlite3.connect(reference.database) as connection:
        schemas = " ".join(
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        )
    assert "WITHOUT ROWID" not in schemas.upper()
    reference.close()


def test_mcp_reference_tools_are_local_and_never_call_the_virtuoso_socket(
    reference_root: Path,
) -> None:
    class NoSocketClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def call(self, method: str, arguments: dict[str, Any]):
            self.calls.append((method, arguments))
            raise AssertionError("local reference tools must not call the Virtuoso socket")

        def close(self) -> None:
            pass

    client = NoSocketClient()
    reference = SkillReference(reference_root)
    server = McpServer(client, skill_reference=reference)

    for request_id, name, arguments in (
        (1, "search_skill_api", {"query": "substring"}),
        (2, "get_skill_api", {"name": "substring", "max_chars": 1_024}),
    ):
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        assert response["result"]["isError"] is False
        assert json.loads(response["result"]["content"][0]["text"])["matches"]

    assert client.calls == []
    reference.close()


def test_mcp_returns_a_bounded_error_for_an_oversized_reference_response(
    reference_root: Path,
) -> None:
    body = "中" * MAX_DOCUMENT_CHARS
    rows = [
        (
            f"oversized/{index}",
            "oversized",
            "oversized()",
            "large",
            "[]",
            "t",
            "",
            "Test Reference",
            f"test/{index}.html",
            "Oversized",
            "test",
            body,
        )
        for index in range(MAX_EXACT_MATCHES)
    ]
    with sqlite3.connect(reference_root / "skill_api.sqlite3") as connection:
        connection.executemany(
            "INSERT INTO functions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )

    class NoSocketClient:
        def call(self, _method: str, _arguments: dict[str, Any]):
            raise AssertionError("local reference tools must not call the Virtuoso socket")

        def close(self) -> None:
            pass

    reference = SkillReference(reference_root)
    server = McpServer(NoSocketClient(), skill_reference=reference)
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_skill_api", "arguments": {"name": "oversized"}},
        }
    )

    assert response["result"]["isError"] is True
    detail = json.loads(response["result"]["content"][0]["text"])
    assert detail["code"] == "response_too_large"
    reference.close()
