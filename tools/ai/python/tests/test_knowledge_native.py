"""Pinned Codex discovery/read with frozen local/remote knowledge selections."""

import json
from pathlib import Path

import pytest
from knowledge_fixtures import docs_server, local, manifest
from sico.codex.backend import CodexBackend
from sico.knowledge.engine import Knowledge
from sico.storage.journal import SessionJournal
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
    registry,
    settings,
)


@pytest.mark.parametrize("tool_format", ["native", "flat"])
def test_native_knowledge_search_read_inventory_credentials_and_resume(
    tmp_path, gateway, native_codex, tool_format  # noqa: F811
):
    source = local(tmp_path / "docs")
    (Path(source["path"]) / "local.md").write_text(
        "MCP tools：仅为文档参考。nmos_1v8 未经结构化资格确认。"
    )
    with docs_server(bearer="remote-sentinel") as (_server, remote):
        remote["credential_ref"] = "KNOWLEDGE_TOKEN"
        value = manifest(source, remote)
        assert (
            Knowledge(tmp_path, value, {"KNOWLEDGE_TOKEN": "remote-sentinel"}).build("local")[
                "status"
            ]
            == "ready"
        )
        path = tmp_path / "resources.json"
        path.write_text(json.dumps(value))
        provider = settings(gateway, native_codex, tool_format)
        provider.options["external_resources"] = str(path)
        provider.environment["KNOWLEDGE_TOKEN"] = "remote-sentinel"
        for number in range(2):
            with SessionJournal(tmp_path, "knowledge") as journal:
                backend = CodexBackend(provider, registry([]), journal, CONTEXT)
                try:
                    gateway.responses.put(answer("Selected knowledge checked."))
                    assert (
                        list(backend.run("Inspect available knowledge."))[-1]["kind"]
                        == "task.completed"
                    )
                    metric = next(
                        m
                        for m in backend.resources.published["mcp_metrics"]
                        if m["name"] == "sico_knowledge"
                    )
                    assert metric["count"] == 2 and metric["array_compact_bytes"] < 2500
                    assert backend.resources.published["configuration"]["pending"] == bool(number)
                    result = backend.runtime.rpc.request(
                        "mcpServer/tool/call",
                        {
                            "threadId": backend.thread_id,
                            "server": "sico_knowledge",
                            "tool": "search",
                            "arguments": {"query": "MCP tools", "limit": 4},
                        },
                        timeout=20,
                    )
                    search = json.loads(result["content"][0]["text"])
                    assert {h["source_id"] for h in search["hits"]} == {"local", "remote"}, search
                    hit = next(h for h in search["hits"] if h["source_id"] == "local")
                    result = backend.runtime.rpc.request(
                        "mcpServer/tool/call",
                        {
                            "threadId": backend.thread_id,
                            "server": "sico_knowledge",
                            "tool": "read",
                            "arguments": {"source_id": "local", "evidence_id": hit["evidence_id"]},
                        },
                        timeout=20,
                    )
                    assert json.loads(result["content"][0]["text"])["verified"]
                    assert "remote-sentinel" not in json.dumps(journal.events())
                    assert (
                        "remote-sentinel" not in (backend.runtime.home / "config.toml").read_text()
                    )
                    assert "KNOWLEDGE_TOKEN" not in backend.runtime.resource_environment_names
                    assert "CDF parameter rules" in str(gateway.requests[-1])
                finally:
                    backend.close()
            # Old thread retains both sources; a new thread will read the new selection.
            value["knowledge_sources"] = []
            path.write_text(json.dumps(value))
        value["knowledge_sources"] = [{**remote, "enabled": False}]
        path.write_text(json.dumps(value))
        with SessionJournal(tmp_path, "new-knowledge") as journal:
            backend = CodexBackend(provider, registry([]), journal, CONTEXT)
            try:
                gateway.responses.put(answer("New selection checked."))
                assert list(backend.run("Inspect new defaults."))[-1]["kind"] == "task.completed"
                assert not backend.runtime.knowledge
                assert "KNOWLEDGE_TOKEN" not in backend.runtime.resource_environment_names
                assert not any(
                    m["name"] == "sico_knowledge"
                    for m in backend.resources.published["mcp_metrics"]
                )
            finally:
                backend.close()
