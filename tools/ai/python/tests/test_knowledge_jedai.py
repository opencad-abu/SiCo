"""JedAI AAI adapter: category status, bounded mapping, verified read, isolation."""

import json
import threading
import time
from pathlib import Path

import pytest
from knowledge_fixtures import jedai, jedai_server, local, manifest
from sico.agent_resources.schema import require_supported, validate_manifest
from sico.knowledge.contracts import KnowledgeError, digest
from sico.knowledge.engine import Knowledge


def test_status_requires_token_and_reports_missing_category(tmp_path):
    with jedai_server(bearer="s3cret") as (_server, source):
        source["credential_ref"] = "JEDAI_TOKEN"
        missing = Knowledge(tmp_path, manifest(source))
        assert missing.status()["sources"][0]["status"] == "auth_required"
        wrong = Knowledge(tmp_path, manifest(source), {"JEDAI_TOKEN": "wrong"})
        assert wrong.status()["sources"][0]["status"] == "auth_required"
        engine = Knowledge(tmp_path, manifest(source), {"JEDAI_TOKEN": "s3cret"})
        assert engine.status()["sources"][0]["status"] == "ready"
        source["collection"] = "missing-docs-26.10"
        absent = Knowledge(tmp_path, manifest(source), {"JEDAI_TOKEN": "s3cret"})
        assert absent.status()["sources"][0]["status"] == "unavailable"


@pytest.mark.parametrize("sse", [False, True])
def test_search_maps_jedai_hits_and_read_verifies_content(tmp_path, sse):
    secret = "jedai-test-token"
    with jedai_server(bearer=secret) as (server, source):
        server.sse = sse
        source["credential_ref"] = "JEDAI_TOKEN"
        engine = Knowledge(tmp_path, manifest(source), {"JEDAI_TOKEN": secret})
        result = engine.search("ViraStack environment variables", limit=4)
        assert result["status"] == "ready" and len(result["hits"]) == 2
        first, second = result["hits"]
        assert first["uri"] == "cda://doc/viraStack/viraStack.pdf"
        assert second["uri"] == "https://support.example/solution/1?id=setup"
        assert first["title"] == "viraStack.pdf" and first["location"]["page"] is None
        assert first["excerpt"].startswith("ViraStack")
        assert first["version"] == digest(server.results[0]["page_content"].encode())
        assert secret not in json.dumps(result) and "query" not in json.dumps(result)
        calls = [message["params"]["arguments"] for message, _headers in server.seen
                 if message["method"] == "tools/call"]
        assert calls[0]["categories"] == ["ask-docs-26.10"] and calls[0]["k"] == 16
        value = engine.read("jedai", second["evidence_id"])
        assert value["verified"] and value["text"] == server.results[1]["page_content"]
        assert "query" not in value
        server.results[1]["page_content"] += "\nNew revision"
        assert engine.read("jedai", second["evidence_id"])["status"] == "stale_reference"
        assert engine.read("jedai", "a" * 64)["status"] == "stale_reference"


def test_unquotable_and_duplicate_hits_never_become_citations(tmp_path):
    with jedai_server() as (server, source):
        server.results = [
            {"name": "bad", "link": "file:///etc/passwd", "page_content": "x"},
            {"name": "dup", "link": "cda://doc/a.md", "page_content": "same chunk"},
            {"name": "dup", "link": "cda://doc/a.md", "page_content": "same chunk"},
            {"name": "ok", "link": "cda://doc/b.md", "page_content": "unique chunk"},
        ]
        result = Knowledge(tmp_path, manifest(source)).search("chunk", limit=4)
        assert [hit["uri"] for hit in result["hits"]] == ["cda://doc/a.md", "cda://doc/b.md"]
        assert result["status"] == "partial"
        assert result["sources"][0]["failed_documents"] == 1


def test_local_and_jedai_fuse_by_source_rank_with_partial_failure(tmp_path):
    source = local(tmp_path / "docs")
    (Path(source["path"]) / "local.md").write_text("ViraStack environment variables in local notes")
    with jedai_server() as (_server, remote_source):
        engine = Knowledge(tmp_path, manifest(source, remote_source))
        assert engine.build("local")["status"] == "ready"
        result = engine.search("ViraStack environment variables")
        assert result["status"] == "ready"
        assert {hit["source_id"] for hit in result["hits"]} == {"local", "jedai"}
        remote_source["credential_ref"] = "MISSING_JEDAI_TOKEN"
        partial = Knowledge(tmp_path, manifest(source, remote_source)).search("ViraStack")
        assert partial["status"] == "partial" and partial["hits"]
        assert all(hit["source_id"] == "local" for hit in partial["hits"])
        assert next(row for row in partial["sources"]
                    if row["source_id"] == "jedai")["status"] == "auth_required"


def test_timeout_and_cancel_reap_stalled_jedai_worker(tmp_path):
    with jedai_server() as (server, source):
        server.delay = 4
        source["timeout_sec"] = 0.3
        started = time.monotonic()
        result = Knowledge(tmp_path, manifest(source)).search("ViraStack")
        assert time.monotonic() - started < 3
        assert result["sources"][0]["status"] in {"timeout", "unavailable"}
        source["timeout_sec"] = 20
        stop = threading.Event()
        timer = threading.Timer(0.25, stop.set)
        timer.start()
        started = time.monotonic()
        try:
            with pytest.raises(KnowledgeError, match="取消"):
                Knowledge(tmp_path, manifest(source)).search("ViraStack", cancelled=stop.is_set)
            assert time.monotonic() - started < 3
        finally:
            timer.cancel()


@pytest.mark.parametrize("reference", ["OPENAI_API_KEY", "MODEL_KEY", "JEDAI_TOKEN"])
def test_jedai_credential_cannot_alias_model_key(tmp_path, reference):
    with jedai_server() as (_server, source):
        source["credential_ref"] = reference
        with pytest.raises(KnowledgeError, match="模型"):
            Knowledge(tmp_path, manifest(source),
                      {"MODEL_KEY": "model-key", "JEDAI_TOKEN": "model-key"}, "MODEL_KEY")


def test_schema_opens_jedai_and_still_rejects_unknown_adapters():
    value = validate_manifest({"version": 2, "knowledge_sources": [
        jedai("http://127.0.0.1:9/api/aai/mcp/", collection="ask-docs-26.10")]})
    require_supported(value)
    unknown = validate_manifest({"version": 2, "knowledge_sources": [
        jedai("http://127.0.0.1:9/api/aai/mcp/", adapter="other_mcp")]})
    with pytest.raises(ValueError, match="尚未接通"):
        require_supported(unknown)
    wrong_whitelist = validate_manifest({"version": 2, "knowledge_sources": [
        jedai("http://127.0.0.1:9/api/aai/mcp/", adapter="openai_docs_mcp")]})
    with pytest.raises(ValueError, match="all、codex、api"):
        require_supported(wrong_whitelist)
    with pytest.raises(ValueError, match="有界非空文本"):
        validate_manifest({"version": 2, "knowledge_sources": [
            jedai("http://127.0.0.1:9/api/aai/mcp/", collection="")]})
