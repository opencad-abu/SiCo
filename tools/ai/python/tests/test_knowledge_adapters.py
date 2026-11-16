"""Direct OpenAI Docs and JedAI adapter mappings with deterministic remotes."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sico.knowledge import jedai_aai, openai_docs
from sico.knowledge.contracts import KnowledgeError, digest


class Remote:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def tool(self, name, arguments):
        self.calls.append((name, arguments))
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def deadline():
    return __import__("time").monotonic() + 10


@pytest.mark.parametrize(
    ("url", "collection", "allowed"),
    [
        ("https://developers.openai.com/api/docs", "all", True),
        ("https://learn.chatgpt.com/docs/mcp", "codex", True),
        ("https://developers.openai.com/codex/a", "codex", True),
        ("https://platform.openai.com/api/a", "api", True),
        ("http://developers.openai.com/docs", "all", False),
        ("https://evil.example/docs", "all", False),
        ("https://developers.openai.com/docs?q=1", "all", False),
        ("https://developers.openai.com/docs/../private", "all", False),
        ("https://developers.openai.com/docs/a", "api", False),
    ],
)
def test_openai_allowed_links_are_host_collection_and_path_bounded(url, collection, allowed):
    assert (openai_docs.allowed(url, collection) is not None) is allowed


def test_openai_status_and_search_mapping_are_direct(monkeypatch):
    assert openai_docs.status({"credential_ref": None}, {}, lambda: False, deadline()) == {
        "status": "unchecked", "message": "搜索时验证远程服务及原文引用",
    }
    remote = Remote(
        json.dumps({"hits": [
            {"url": "https://developers.openai.com/docs/a", "hierarchy": {"lvl1": "Docs"}},
            {"url": "https://developers.openai.com/docs/a"},
            {"url": "file:///etc/passwd"},
        ]}),
        "MCP tools text\nsecond line",
    )
    monkeypatch.setattr(openai_docs, "client", lambda *_args: remote)
    monkeypatch.setattr(openai_docs, "remember", lambda _project, _source, rows: rows)
    source = {"id": "remote", "collection": "all"}
    rows, state = openai_docs.search(
        "project", source, "MCP tools", ["mcp", "tools"], 2, {}, lambda: False, deadline()
    )
    assert state == {"status": "ready", "failed_documents": 0, "message": ""}
    assert len(rows) == 1 and rows[0]["title"] == "Docs"
    assert rows[0]["uri"] == "https://developers.openai.com/docs/a"
    assert rows[0]["location"]["start"] == 1


def test_openai_search_marks_unreadable_remote_as_partial(monkeypatch):
    remote = Remote(
        json.dumps({"hits": [{"url": "https://developers.openai.com/docs/a"}]}),
        KnowledgeError("unavailable", "down"),
    )
    monkeypatch.setattr(openai_docs, "client", lambda *_args: remote)
    monkeypatch.setattr(openai_docs, "remember", lambda _project, _source, rows: rows)
    rows, state = openai_docs.search(
        "project", {"id": "remote", "collection": "all"}, "q", ["q"], 2,
        {}, lambda: False, deadline(),
    )
    assert rows == [] and state["status"] == "unavailable"
    assert state["failed_documents"] == 1


def test_openai_read_verifies_version_and_rejects_scope(monkeypatch):
    text = "MCP tools text\nsecond line"
    row = {
        "uri": "https://developers.openai.com/docs/a",
        "version": digest(text.encode()),
        "position": 0,
    }
    monkeypatch.setattr(openai_docs, "lookup", lambda *_args: dict(row))
    remote = Remote(text)
    monkeypatch.setattr(openai_docs, "client", lambda *_args: remote)
    result = openai_docs.read("project", {"collection": "all"}, "id", {}, lambda: False, deadline())
    assert result["verified"] and result["text"] == text

    monkeypatch.setattr(openai_docs, "lookup",
                        lambda *_args: {**row, "version": "old"})
    monkeypatch.setattr(openai_docs, "client", lambda *_args: Remote(text))
    with pytest.raises(KnowledgeError, match="已变更") as error:
        openai_docs.read("project", {"collection": "all"}, "id", {}, lambda: False, deadline())
    assert error.value.code == "stale_reference"

    monkeypatch.setattr(openai_docs, "lookup",
                        lambda *_args: {**row, "uri": "file:///etc/passwd"})
    with pytest.raises(KnowledgeError, match="集合范围") as error:
        openai_docs.read("project", {"collection": "all"}, "id", {}, lambda: False, deadline())
    assert error.value.code == "invalid_reference"


@pytest.mark.parametrize(
    ("link", "valid"),
    [
        ("cda://doc/a.md", True),
        ("https://support.example/a?id=1", True),
        ("http://support.example/a", True),
        ("file:///etc/passwd", False),
        ("https://user:pass@example/a", False),
        ("cda://", False),
        ("https://example/a b", False),
    ],
)
def test_jedai_links_are_citation_safe(link, valid):
    assert jedai_aai.valid_link(link) is valid


def test_jedai_status_categories_and_missing_collection(monkeypatch):
    remote = Remote(json.dumps([
        {"category": "ask-docs"}, {"category": "other"}, {"not": "category"},
    ]))
    monkeypatch.setattr(jedai_aai, "client", lambda *_args: remote)
    assert jedai_aai.status({"collection": "ask-docs"}, {}, lambda: False, deadline()) == {
        "status": "ready", "message": "认证与集合可用",
    }
    missing = remote = Remote(json.dumps([{"category": "other"}]))
    monkeypatch.setattr(jedai_aai, "client", lambda *_args: missing)
    assert jedai_aai.status({"collection": "ask-docs"}, {}, lambda: False, deadline())[
        "status"
    ] == "unavailable"


def test_jedai_search_maps_deduplicates_and_marks_partial(monkeypatch):
    remote = Remote(json.dumps({"results": [
        {"name": "a", "link": "cda://doc/a.md", "page_content": "same"},
        {"name": "a2", "link": "cda://doc/a.md", "page_content": "same"},
        {"name": "bad", "link": "file:///x", "page_content": "bad"},
        {"name": "b", "link": "cda://doc/b.md", "page_content": "unique"},
    ]}))
    monkeypatch.setattr(jedai_aai, "client", lambda *_args: remote)
    monkeypatch.setattr(jedai_aai, "remember", lambda _project, _source, rows: rows)
    rows, state = jedai_aai.search(
        "project", {"id": "jedai", "collection": "ask"}, "query", ["query"], 4,
        {}, lambda: False, deadline(),
    )
    assert [row["uri"] for row in rows] == ["cda://doc/a.md", "cda://doc/b.md"]
    assert state["status"] == "partial" and state["failed_documents"] == 1
    assert "query" not in rows[0]


def test_jedai_read_verifies_query_and_content(monkeypatch):
    text = "verified content"
    row = {"uri": "cda://doc/a.md", "version": digest(text.encode()), "query": "q"}
    monkeypatch.setattr(jedai_aai, "lookup", lambda *_args: dict(row))
    remote = Remote(json.dumps({"results": [
        {"link": "cda://doc/a.md", "page_content": text}
    ]}))
    monkeypatch.setattr(jedai_aai, "client", lambda *_args: remote)
    result = jedai_aai.read("project", {"collection": "ask"}, "id", {}, lambda: False, deadline())
    assert result["verified"] and result["text"] == text

    monkeypatch.setattr(jedai_aai, "lookup", lambda *_args: {**row, "query": ""})
    with pytest.raises(KnowledgeError, match="缺少可复核") as error:
        jedai_aai.read("project", {"collection": "ask"}, "id", {}, lambda: False, deadline())
    assert error.value.code == "stale_reference"
