"""Local retrieval quality, scope, source freshness and atomic index replacement."""

import json
import time
from pathlib import Path

import pytest
from knowledge_fixtures import local, manifest
from sico.agent_resources import store
from sico.knowledge import index, local_query
from sico.knowledge.catalog import directory
from sico.knowledge.contracts import KnowledgeError, query_terms
from sico.knowledge.engine import Knowledge


def corpus(tmp_path):
    source = local(tmp_path / "docs")
    docs = {
        "voltage.md": "# 薄栅氧电压\n薄栅氧晶体管绝不能超过额定电压。",
        "device.md": "# nmos_1v8\n器件 nmos_1v8 参数 vth0，版本 1.2.3，pin VDD! OUT<0>。",
        "skill.md": "# Cadence SKILL\ndbOpenCellViewByType 后台创建辅助测试视图。",
        "mcp.md": "# MCP tools\nSTDIO and Streamable HTTP support for Codex.",
    }
    for name, text in docs.items():
        (Path(source["path"]) / name).write_text(text)
    index.build(tmp_path, source)
    return source


@pytest.mark.parametrize(
    "query,expected",
    [
        ("薄栅氧电压限制", "voltage.md"),
        ("nmos_1v8", "device.md"),
        ("vth0 1.2.3", "device.md"),
        ("OUT<0> VDD!", "device.md"),
        ("dbOpenCellViewByType", "skill.md"),
        ("Streamable HTTP", "mcp.md"),
    ],
)
def test_fixed_multilingual_queries_return_expected_top_document(tmp_path, query, expected):
    source = corpus(tmp_path)
    rows, state = local_query.search(
        tmp_path, source, query_terms(query), 5, lambda: False, time.monotonic() + 10
    )
    assert state["status"] == "ready" and rows[0]["title"] == expected
    proof = local_query.read(
        tmp_path, source, rows[0]["evidence_id"], lambda: False, time.monotonic() + 10
    )
    assert proof["verified"] and proof["version"] == rows[0]["version"]
    assert proof["text"] in (Path(source["path"]) / expected).read_text()


def test_index_reuses_unchanged_docs_drops_removed_and_invalidates_old_references(tmp_path):
    source = corpus(tmp_path)
    engine = Knowledge(tmp_path, manifest(source))
    first = engine.search("薄栅氧")["hits"][0]
    assert engine.build(source["id"])["reused"] == 4
    path = Path(source["path"])
    (path / "voltage.md").write_text("# 新电压规则\n厚栅氧资料，仅供参考。")
    (path / "skill.md").unlink()
    assert engine.read(source["id"], first["evidence_id"])["status"] == "stale_reference"
    assert engine.search("薄栅氧")["sources"][0]["status"] == "stale"
    result = engine.build(source["id"])
    assert result["parsed"] == 1 and result["reused"] == 2 and result["removed"] == 1
    assert engine.read(source["id"], first["evidence_id"])["status"] == "stale_reference"


def test_failed_build_and_cancel_preserve_previous_generation(tmp_path):
    source = corpus(tmp_path)
    path = directory(tmp_path, source) / "index.sqlite3"
    before = path.read_bytes()
    (Path(source["path"]) / "bad.txt").write_bytes(b"\xff\x00")
    with pytest.raises(KnowledgeError, match="UTF-8"):
        index.build(tmp_path, source)
    assert path.read_bytes() == before
    with pytest.raises(KnowledgeError, match="取消"):
        index.build(tmp_path, source, lambda: True)
    assert path.read_bytes() == before
    assert not list(path.parent.glob("generation-*"))


def test_symlinks_exclusions_disabled_sources_and_unknown_reads(tmp_path):
    source = local(tmp_path / "docs")
    path = Path(source["path"])
    (path / "public.md").write_text("public design docs")
    (path / "private.md").write_text("SECRET_SENTINEL")
    (path / "escape.md").symlink_to(path / "private.md")
    source["exclude"] = ["private.*"]
    engine = Knowledge(tmp_path, manifest(source))
    assert engine.build("local")["documents"] == 1
    assert engine.search("SECRET_SENTINEL")["no_matches"]
    with pytest.raises(KnowledgeError, match="引用标识"):
        engine.read("local", "/etc/passwd")
    source["enabled"] = False
    with pytest.raises(KnowledgeError, match="未在当前选择"):
        Knowledge(tmp_path, manifest(source)).read("local", "a" * 64)


def test_schema_opens_supported_knowledge_but_rejects_unknown_adapter(tmp_path):
    source = local(tmp_path / "docs")
    saved = store.save_project(tmp_path, manifest(source), expected_revision=None)
    assert saved["manifest"]["knowledge_sources"] == [source]
    assert Path(saved["path"]).stat().st_mode & 0o077 == 0
    engine = Knowledge(tmp_path, manifest(source))
    assert engine.status()["sources"][0]["status"] == "pending_index"
    assert not directory(tmp_path, source).exists()


def test_pdf_page_locations_and_ocr_required_are_explicit(tmp_path):
    from knowledge_fixtures import pdf_document

    source = local(tmp_path / "docs")
    (Path(source["path"]) / "manual.pdf").write_bytes(pdf_document())
    result = Knowledge(tmp_path, manifest(source)).build("local")
    assert result["status"] == "partial" and result["issues"][0]["page"] == 2
    hit = Knowledge(tmp_path, manifest(source)).search("nmos_1v8")["hits"][0]
    assert hit["location"]["page"] == 1 and hit["uri"].endswith("#page=1")
    assert "ocr_required" in json.dumps(result)
