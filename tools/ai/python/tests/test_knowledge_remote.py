"""Remote wire contracts, citation validation, fan-out failure and cancellation."""

import json
import threading
import time
from pathlib import Path

import pytest
from knowledge_fixtures import docs_server, local, manifest
from sico.knowledge.contracts import KnowledgeError
from sico.knowledge.engine import Knowledge


@pytest.mark.parametrize("sse", [False, True])
def test_remote_authenticated_search_fetch_read_and_version_drift(tmp_path, sse):
    secret = "private-test-token"
    with docs_server(bearer=secret) as (server, source):
        server.sse = sse
        source["credential_ref"] = "DOCS_TOKEN"
        missing = Knowledge(tmp_path, manifest(source))
        assert missing.search("Streamable")["sources"][0]["status"] == "auth_required"
        engine = Knowledge(tmp_path, manifest(source), {"DOCS_TOKEN": secret})
        result = engine.search("Streamable HTTP")
        assert result["status"] == "ready" and len(result["hits"]) == 1
        hit = result["hits"][0]
        assert secret not in json.dumps(result) and hit["version"] and hit["location"]["start"] == 1
        assert all(h["Authorization"] == "Bearer " + secret for _v, h in server.seen)
        assert engine.read("remote", hit["evidence_id"])["verified"]
        server.documents[hit["uri"]] += "\nNew revision"
        assert engine.read("remote", hit["evidence_id"])["status"] == "stale_reference"
        assert engine.read("remote", "a" * 64)["status"] == "stale_reference"


def test_unknown_urls_collections_and_failed_fetches_never_become_read_authority(tmp_path):
    with docs_server() as (server, source):
        server.hits = [
            {"url": url}
            for url in (
                "file:///etc/passwd",
                "https://evil.example/private",
                "https://developers.openai.com@evil.example",
                "https://learn.chatgpt.com/docs/../../private",
                "https://learn.chatgpt.com/docs/missing",
            )
        ]
        result = Knowledge(tmp_path, manifest(source)).search("MCP")
        assert not result["hits"] and result["sources"][0]["status"] == "unavailable"
        calls = [
            m["params"]["arguments"].get("url")
            for m, _h in server.seen
            if m["method"] == "tools/call"
        ]
        assert calls == [None, "https://learn.chatgpt.com/docs/missing"]
        source["adapter"] = "arbitrary"
        with pytest.raises(ValueError, match="尚未接通"):
            Knowledge(tmp_path, manifest(source))


def test_fusion_preserves_sources_and_partial_failure_with_local_hits(tmp_path):
    source = local(tmp_path / "docs")
    (Path(source["path"]) / "local.md").write_text("MCP tools and Streamable HTTP")
    with docs_server() as (server, remote):
        engine = Knowledge(tmp_path, manifest(source, remote))
        assert engine.build("local")["status"] == "ready"
        result = engine.search("MCP tools")
        assert result["status"] == "ready"
        assert [r["source_id"] for r in result["hits"]] == ["local", "remote"]
        remote["credential_ref"] = "MISSING_TOKEN"
        partial = Knowledge(tmp_path, manifest(source, remote)).search("MCP")
        assert partial["status"] == "partial" and partial["hits"][0]["source_id"] == "local"
        assert (
            next(s for s in partial["sources"] if s["source_id"] == "remote")["status"]
            == "auth_required"
        )


def test_timeout_and_cancel_reap_stalled_remote_worker(tmp_path):
    with docs_server() as (server, source):
        server.delay = 4
        source["timeout_sec"] = 0.3
        started = time.monotonic()
        result = Knowledge(tmp_path, manifest(source)).search("MCP")
        assert time.monotonic() - started < 3
        assert result["sources"][0]["status"] in {"timeout", "unavailable"}
        source["timeout_sec"] = 20
        stop = threading.Event()
        timer = threading.Timer(0.25, stop.set)
        timer.start()
        started = time.monotonic()
        try:
            with pytest.raises(KnowledgeError, match="取消"):
                Knowledge(tmp_path, manifest(source)).search("MCP", cancelled=stop.is_set)
            assert time.monotonic() - started < 3
        finally:
            timer.cancel()


@pytest.mark.parametrize("reference", ["OPENAI_API_KEY", "MODEL_KEY", "DOCS_TOKEN"])
def test_resource_credential_cannot_alias_model_key(tmp_path, reference):
    with docs_server() as (_server, source):
        source["credential_ref"] = reference
        with pytest.raises(KnowledgeError, match="模型"):
            Knowledge(
                tmp_path,
                manifest(source),
                {"MODEL_KEY": "model-key", "DOCS_TOKEN": "model-key"},
                "MODEL_KEY",
            )
