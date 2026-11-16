"""Session-owned knowledge MCP bridge and bounded search/read execution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sico.codex import knowledge_runtime
from sico.knowledge.contracts import KnowledgeError


class Bridge:
    def __init__(self):
        self.port, self.token = 1234, "token"
        self.closed = 0

    def close(self):
        self.closed += 1


class Engine:
    def __init__(self):
        self.references = ["ref"]
        self.calls = []
        self.error = None

    def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        if self.error:
            raise self.error
        return {"status": "ready", "hits": []}

    def read(self, **kwargs):
        self.calls.append(("read", kwargs))
        return {"status": "ready", "verified": True}


def install(monkeypatch):
    engine = Engine()
    bridge = Bridge()
    monkeypatch.setattr(knowledge_runtime, "Knowledge", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(knowledge_runtime, "ToolBridge",
                        lambda _owner, _execute, timeout: bridge)
    monkeypatch.setattr(knowledge_runtime, "python_path", lambda: "/python")
    monkeypatch.setattr(knowledge_runtime, "cad_python", lambda _env: "/python3")
    monkeypatch.setattr(knowledge_runtime, "schemas", lambda: [{"name": "search"}])
    value = knowledge_runtime.KnowledgeRuntime(
        "/project", {"version": 2}, SimpleNamespace(
            environment={"HOME": "/home"}, options={}))
    return value, engine, bridge


def test_constructor_publishes_bounded_mcp_configuration(monkeypatch):
    value, engine, bridge = install(monkeypatch)
    assert value.engine is engine and value.bridge is bridge
    assert value.environment == {"SICO_KNOWLEDGE_PORT": "1234",
                                 "SICO_KNOWLEDGE_TOKEN": "token"}
    assert value.config["enabled_tools"] == ["search", "read"]
    assert value.config["default_tools_approval_mode"] == "approve"
    assert value.config["env"]["SICO_MCP_TIMEOUT"] == "70"
    assert value.mcp_schemas() == [{"name": "search"}]


def test_execute_search_and_read_returns_text_content(monkeypatch):
    value, engine, _bridge = install(monkeypatch)
    result = value.execute("search", {"query": "x", "limit": 2})
    assert result["isError"] is False
    assert result["content"][0]["text"].startswith('{"status": "ready"')
    assert engine.calls[-1][0] == "search"
    result = value.execute("read", {"source_id": "s", "evidence_id": "e"})
    assert result["isError"] is False
    assert engine.calls[-1][0] == "read"


def test_execute_rejects_invalid_arguments_and_knowledge_errors(monkeypatch):
    value, engine, _bridge = install(monkeypatch)
    assert value.execute("search", [])["isError"] is True
    assert value.execute("search", {"query": "x", "extra": 1})["isError"] is True
    assert value.execute("read", {"source_id": "s"})["isError"] is True
    assert value.execute("other", {})["isError"] is True
    engine.error = KnowledgeError("missing", "not found", category="invalid")
    result = value.execute("search", {"query": "x"})
    assert result["isError"] is True
    assert '"error_code": "invalid"' in result["content"][0]["text"]


def test_execute_respects_stop_and_capacity(monkeypatch):
    value, _engine, _bridge = install(monkeypatch)
    value.stop.set()
    with pytest.raises(ValueError, match="知识源"):
        value.execute("search", {"query": "x"})
    value.stop.clear()
    value.active = 2
    with pytest.raises(ValueError, match="知识源"):
        value.execute("search", {"query": "x"})
    value.active = 0


def test_close_stops_bridge_and_rejects_unfinished_checks(monkeypatch):
    value, _engine, bridge = install(monkeypatch)
    value.close()
    assert value.stop.is_set() and bridge.closed == 1
    value, _engine, bridge = install(monkeypatch)
    value.active = 1
    value.lock.wait_for = lambda _predicate, timeout: False
    with pytest.raises(RuntimeError, match="尚未确认"):
        value.close()
