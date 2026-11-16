from __future__ import annotations

import json

import pytest

from cadai.document_tools import (
    DocumentError,
    DOCUMENT_TOOLS,
    list_documents,
    read_document,
    search_documents,
)
from cadai.mcp import McpServer, _TOOLS_BY_NAME
from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext, ToolCall
from sico.storage.journal import SessionJournal


def bound(workspace):
    return BoundContext("instance", "generation", "target", {"cwd": str(workspace)})


class Offline:
    def close(self):
        pass


def test_shared_registration_matches_direct_mcp(tmp_path):
    with SessionJournal(tmp_path, "documents") as journal:
        registry = context_tools(Offline(), journal)
        schemas = {tool["name"]: tool for tool in registry.schemas()}
        for definition in DOCUMENT_TOOLS:
            shared = schemas[definition["name"]]
            assert shared["input_schema"] == definition["inputSchema"]
            assert shared["description"] == definition["description"]
            assert shared["annotations"] == definition["annotations"]
            assert _TOOLS_BY_NAME[definition["name"]]["inputSchema"] == definition["inputSchema"]


def test_list_read_and_search_are_bounded_and_return_digest(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("alpha\nneedle here\n" + "tail\n" * 20, encoding="utf-8")
    (docs / "data.json").write_text('{"needle": true}\n', encoding="utf-8")
    (docs / "skip.py").write_text("needle\n", encoding="utf-8")

    listed = list_documents({"path": "docs", "globs": ["*.md", "*.json"]}, tmp_path)
    assert listed["complete"] and {row["path"] for row in listed["results"]} == {
        "docs/data.json", "docs/guide.md"
    }
    page = read_document({"path": "docs/guide.md", "offset": 6, "limit": 6}, tmp_path)
    assert page["text"] == "needle" and page["sha256"]
    assert page["next_offset"] == 12 and not page["complete"]
    matches = search_documents({"path": "docs", "pattern": "needle", "globs": ["*.md"]}, tmp_path)
    assert matches["returned"] == 1 and matches["results"][0]["line_number"] == 2


@pytest.mark.parametrize("document_args", [
    {"path": "../secret.txt"},
    {"path": "/etc/passwd"},
    {"path": "docs"},
])
def test_read_rejects_escape_and_non_regular_files(tmp_path, document_args):
    (tmp_path / "docs").mkdir()
    (tmp_path.parent / "secret.txt").write_text("secret", encoding="utf-8")
    if document_args["path"] == "/etc/passwd":
        # The exact host file is still outside the captured workspace.
        pass
    with pytest.raises(DocumentError):
        read_document(document_args, tmp_path)


def test_symlink_escape_and_binary_are_rejected(tmp_path):
    outside = tmp_path.parent / "outside-document.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "escape.txt").symlink_to(outside)
    (tmp_path / "binary.bin").write_bytes(b"header\x00payload")
    with pytest.raises(DocumentError):
        read_document({"path": "escape.txt"}, tmp_path)
    with pytest.raises(DocumentError):
        read_document({"path": "binary.bin"}, tmp_path)


def test_shared_tool_executes_without_virtuoso_and_mcp_dispatches(tmp_path):
    (tmp_path / "README.md").write_text("Copilot documentation", encoding="utf-8")
    with SessionJournal(tmp_path, "dispatch") as journal:
        registry = context_tools(Offline(), journal)
        result = registry.execute(
            ToolCall("read", "read_project_document", {"path": "README.md"}),
            bound(tmp_path),
        )
        assert result.status == "ok" and result.data["text"] == "Copilot documentation"
    server = McpServer(Offline(), workspace=tmp_path)
    response = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "read_project_document", "arguments": {"path": "README.md"}},
    })
    assert not response["result"]["isError"]
    assert json.loads(response["result"]["content"][0]["text"])["sha256"]
