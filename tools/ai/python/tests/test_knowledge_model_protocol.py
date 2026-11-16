"""Model-requested search/read, default-approved citations and recovery (scripted model)."""

import json
from pathlib import Path

import pytest
from knowledge_fixtures import docs_server, local, manifest
from knowledge_model_harness import (
    configure_document_session,
    final_text,
    knowledge_calls,
    run_turn,
    tool_value,
)
from sico.knowledge.engine import Knowledge
from sico.service.backend import create_backend
from sico.storage.journal import SessionJournal
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,  # noqa: F811
    native_codex,  # noqa: F811
    registry,
    settings,
)
from test_codex_resources_runtime import output, tool_call


def decoded(request, call_id):
    value = output(request, call_id)
    if isinstance(value, list):
        value = next(row["text"] for row in value if row.get("text", "").startswith("{"))
    return json.loads(value)


def search_request(request):
    return tool_call(
        request, "mcp__sico_knowledge", "search", "search", {"query": "MCP tools", "limit": 4}
    )


def queue_cited_answer(server, proof):
    def read_local(request):
        search = decoded(request, "search")
        proof["search"] = search
        hit = next(hit for hit in search["hits"] if hit["source_id"] == "local")
        return tool_call(
            request,
            "mcp__sico_knowledge",
            "read",
            "local-read",
            {"source_id": "local", "evidence_id": hit["evidence_id"]},
        )

    def read_remote(request):
        proof["local"] = decoded(request, "local-read")
        hit = next(hit for hit in proof["search"]["hits"] if hit["source_id"] == "remote")
        return tool_call(
            request,
            "mcp__sico_knowledge",
            "read",
            "remote-read",
            {"source_id": "remote", "evidence_id": hit["evidence_id"]},
        )

    def cite(request):
        proof["remote"] = decoded(request, "remote-read")
        return answer(
            "\n".join(
                f"{row['uri']} L{row['location']['start']}-{row['location']['end']} "
                f"sha256:{row['version']}"
                for row in (proof["local"], proof["remote"])
            )
        )

    for response in (search_request, read_local, read_remote, cite):
        server.responses.put(response)


@pytest.mark.parametrize("tool_format", ["native", "flat"])
def test_model_calls_both_sources_reads_cites_and_resumes_frozen_selection(
    tmp_path,
    gateway,  # noqa: F811
    native_codex,  # noqa: F811
    tool_format,  # noqa: F811
):
    source = local(tmp_path / "docs")
    (Path(source["path"]) / "manual.md").write_text("# MCP tools\nLocal protocol reference.")
    with docs_server() as (_remote, remote):
        value = manifest(source, remote)
        assert Knowledge(tmp_path, value).build("local")["status"] == "ready"
        path = tmp_path / "resources.json"
        path.write_text(json.dumps(value))
        provider = settings(gateway, native_codex, tool_format)
        provider.options["external_resources"] = str(path)
        identity = None
        for resuming in (False, True):
            proof = {}
            queue_cited_answer(gateway, proof)
            with SessionJournal(tmp_path, "model-knowledge") as journal:
                configure_document_session(journal, CONTEXT)
                backend = create_backend(provider, registry([]), journal, CONTEXT)
                try:
                    result = run_turn(backend, "解释 MCP tools 并核验本地和远程引用。", timeout=30)
                    assert result["status"] == "completed", result
                    calls = knowledge_calls(result)
                    assert [row["tool"] for row in calls] == ["search", "read", "read"]
                    assert all(row["status"] == "completed" for row in calls)
                    assert all(tool_value(row).get("verified") for row in calls[1:])
                    text = final_text(result)
                    for row in (proof["local"], proof["remote"]):
                        assert row["verified"] and row["uri"] in text and row["version"] in text
                    assert not [
                        r
                        for r in backend.audit.index.audits.values()
                        if r["task_id"] == backend.state.task["id"] and r.get("elicitation")
                    ], "read-only knowledge tools must not prompt for approval"
                    assert result["resources"]["configuration"]["pending"] == resuming
                    assert bool(result["prior_search_hits"]) == resuming
                    if resuming:
                        assert identity == result["thread_id"]
                    identity = result["thread_id"]
                finally:
                    backend.close()
            path.write_text(json.dumps(manifest()))

        def no_knowledge(request):
            assert "mcp__sico_knowledge" not in json.dumps(request["tools"])
            return answer("新会话未启用知识源。")

        gateway.responses.put(no_knowledge)
        with SessionJournal(tmp_path, "new-knowledge") as journal:
            configure_document_session(journal, CONTEXT)
            backend = create_backend(provider, registry([]), journal, CONTEXT)
            try:
                result = run_turn(backend, "检查新会话可用知识源。")
                assert result["status"] == "completed" and not knowledge_calls(result)
                assert backend.runtime.knowledge is None
            finally:
                backend.close()


@pytest.mark.parametrize("tool_format", ["native", "flat"])
def test_model_receives_changed_evidence(
    tmp_path,
    gateway,  # noqa: F811
    native_codex,  # noqa: F811
    tool_format,
):
    source = local(tmp_path / "docs")
    document = Path(source["path"]) / "manual.md"
    document.write_text("MCP tools original text")
    value = manifest(source)
    Knowledge(tmp_path, value).build("local")
    path = tmp_path / "resources.json"
    path.write_text(json.dumps(value))
    provider = settings(gateway, native_codex, tool_format)
    provider.options["external_resources"] = str(path)
    gateway.responses.put(search_request)

    def read_changed(request):
        hit = decoded(request, "search")["hits"][0]
        document.write_text("MCP tools changed text")
        return tool_call(
            request,
            "mcp__sico_knowledge",
            "read",
            "changed",
            {"source_id": "local", "evidence_id": hit["evidence_id"]},
        )

    def reject_stale(request):
        assert decoded(request, "changed")["status"] == "stale_reference"
        return answer("原文已变化，当前引用未核验。")

    gateway.responses.put(read_changed)
    gateway.responses.put(reject_stale)
    with SessionJournal(tmp_path, "stale") as journal:
        configure_document_session(journal, CONTEXT)
        backend = create_backend(provider, registry([]), journal, CONTEXT)
        try:
            result = run_turn(backend, "请核验 MCP tools 原文。", timeout=30)
            assert result["status"] == "completed", result
            assert not any(
                tool_value(row).get("verified")
                for row in knowledge_calls(result)
                if row.get("result") and row["tool"] == "read"
            )
        finally:
            backend.close()


def test_repeated_knowledge_calls_need_no_approval_interactions(
    tmp_path,
    gateway,  # noqa: F811
    native_codex,  # noqa: F811
):
    source = local(tmp_path / "docs")
    (Path(source["path"]) / "manual.md").write_text("MCP tools bounded loop")
    value = manifest(source)
    Knowledge(tmp_path, value).build("local")
    path = tmp_path / "resources.json"
    path.write_text(json.dumps(value))
    provider = settings(gateway, native_codex, "native")
    provider.options["external_resources"] = str(path)
    gateway.responses.put(search_request)
    gateway.responses.put(
        lambda request: tool_call(
            request, "mcp__sico_knowledge", "search", "repeat", {"query": "MCP tools"}
        )
    )
    gateway.responses.put(lambda request: answer("两次检索均已执行。"))
    with SessionJournal(tmp_path, "budget") as journal:
        configure_document_session(journal, CONTEXT)
        backend = create_backend(provider, registry([]), journal, CONTEXT)
        try:
            result = run_turn(backend, "检索 MCP tools。", timeout=20)
            assert result["status"] == "completed", result
            assert [row["tool"] for row in knowledge_calls(result)] == ["search", "search"]
            assert result["approval_count"] == 0
            assert not backend.audit.pending
        finally:
            backend.close()
