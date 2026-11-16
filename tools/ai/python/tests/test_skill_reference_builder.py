from __future__ import annotations

import gzip
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from cadai import skill_reference_store
from cadai.skill_reference import SkillReference


def _corpus(tmp_path: Path) -> Path:
    source = tmp_path / "skill4ai"
    references = source / "references"
    documentation = source / "documentation" / "sklangref"
    references.mkdir(parents=True)
    documentation.mkdir(parents=True)
    (references / "functions.json").write_text(
        json.dumps(
            {
                "substring": {
                    "usage": "substring( S_string x_index [ x_length ] )",
                    "description": "Returns part of a string.",
                    "parameters": [],
                    "returns": "A string.",
                    "example": 'substring("abc" 1 1)',
                }
            }
        ),
        encoding="utf-8",
    )
    (documentation / "substring.html").write_text(
        """<html><head>
<meta name="DocTitle" content="SKILL Language User Guide">
<meta name="ProductVersion" content="ICADVM 23.1">
<meta name="RightsManagement" content="Copyright Cadence">
</head><body><div id="main-content"><h3>substring</h3>
<pre class="syntax">substring( S_string x_index [ x_length ] )</pre>
<h4>Description</h4><p>Returns a substring.</p>
<h4>Arguments</h4><table><tr><td>S_string</td><td>Input string.</td></tr>
<tr><td>x_index</td><td>Start index.</td></tr></table>
<h4>Value Returned</h4><table><tr><td>t_result</td><td>Selected text.</td></tr></table>
<h4>Examples</h4><pre>substring("abc" 1 1)</pre>
<h4>Related Topics</h4><p><a href="string.html">string functions</a></p>
<img src="cadence-logo.png"><script>ignored()</script>
<a href="#pagetop">Return to top</a></div></body></html>""",
        encoding="utf-8",
    )
    return source


def _build(
    source: Path, output: Path, supplement: Path | None = None
) -> subprocess.CompletedProcess[str]:
    builder = Path(__file__).resolve().parents[2] / "reference/cadence-skill/build_reference.py"
    command = [sys.executable, str(builder), str(source), str(output)]
    if supplement is not None:
        command += ["--supplement", str(supplement)]
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )


def test_builder_outputs_are_deterministic_and_logically_equal(tmp_path: Path) -> None:
    source = _corpus(tmp_path)
    first = tmp_path / "first" / "skill_api.sqlite3"
    second = tmp_path / "second" / "skill_api.sqlite3"

    assert _build(source, first).returncode == 0
    assert _build(source, second).returncode == 0
    assert first.read_bytes() == second.read_bytes()
    assert first.with_suffix(".json.gz").read_bytes() == second.with_suffix(
        ".json.gz"
    ).read_bytes()

    payload = json.loads(gzip.decompress(first.with_suffix(".json.gz").read_bytes()))
    with sqlite3.connect(first) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute("SELECT * FROM functions")]
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    runtime_fields = tuple(payload["functions"][0])
    assert payload["functions"] == [
        {field: row[field] for field in runtime_fields} for row in rows
    ]
    assert payload["metadata"] == {
        "function_count": int(metadata["function_count"]),
        "document_count": int(metadata["document_count"]),
        "copyrights": json.loads(metadata["copyrights"]),
    }


def test_builder_checks_both_outputs_before_writing(tmp_path: Path) -> None:
    source = _corpus(tmp_path)
    output = tmp_path / "skill_api.sqlite3"
    fallback = output.with_suffix(".json.gz")
    fallback.write_bytes(b"existing")

    result = _build(source, output)

    assert result.returncode == 2
    assert "JSON output already exists" in result.stderr
    assert not output.exists()
    assert fallback.read_bytes() == b"existing"


def test_builder_extracts_clean_structured_facts(tmp_path: Path) -> None:
    source = _corpus(tmp_path)
    output = tmp_path / "reference" / "skill_api.sqlite3"

    assert _build(source, output).returncode == 0
    with sqlite3.connect(output) as connection:
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute("SELECT * FROM functions").fetchone())

    assert row["kind"] == "function"
    assert row["domain"] == "skill_language"
    assert row["source_kind"] == "html"
    assert "Returns a substring." in row["description"]
    assert json.loads(row["arguments_json"]) == [
        {"name": "S_string", "description": "Input string."},
        {"name": "x_index", "description": "Start index."},
    ]
    assert json.loads(row["returns_json"]) == [
        {"value": "t_result", "description": "Selected text."}
    ]
    assert json.loads(row["examples_json"]) == ['substring("abc" 1 1)']
    assert json.loads(row["related_json"]) == [
        {"name": "string functions", "document": "string.html"}
    ]
    assert "cadence-logo" not in row["body"]
    assert "ignored()" not in row["body"]
    assert "Return to top" not in row["body"]

    reference = SkillReference(output.parent)
    found = reference.search({"query": "substring", "limit": 1})
    assert found["matches"][0]["kind"] == "function"
    assert found["matches"][0]["domain"] == "skill_language"
    scoped = reference.search({
        "query": "substring", "kind": "function", "domain": "skill_language", "limit": 1,
    })
    assert scoped["total_matches"] == 1
    assert reference.search({"query": "substring", "family": "no-such-family"})[
        "total_matches"
    ] == 0
    document = reference.get({"name": "substring"})
    assert document["matches"][0]["arguments"][0]["name"] == "S_string"
    assert document["matches"][0]["examples"] == ['substring("abc" 1 1)']
    reference.close()


def test_builder_merges_clean_supplement_records(tmp_path: Path) -> None:
    source = _corpus(tmp_path)
    output = tmp_path / "reference" / "skill_api.sqlite3"
    supplement = tmp_path / "supplement.json"
    record = {
        "reference_id": "pas_gte_pcell/PasGetLibProps",
        "name": "PasGetLibProps",
        "usage": "PasGetLibProps( S_libName [g_force] ) => a_devicePropertyTable",
        "description": "Retrieves the library CDF device property table.",
        "arguments_json": '[{"name":"S_libName","description":"Library name."}]',
        "returns_json": "[]",
        "examples_json": "[]",
        "related_json": "[]",
        "kind": "function",
        "domain": "pdk_automation",
        "family": "pas",
        "doc_set": "pas_gte_pcell",
        "document": "pas_gte_pcell.pdf#page=221",
        "doc_title": "Cadence PAS GTE PCell Reference Manual",
        "product_version": "03.01.09",
        "source_kind": "pdf",
        "body": "PasGetLibProps S_libName Library name.",
    }
    supplement.write_text(json.dumps([record]), encoding="utf-8")

    assert _build(source, output, supplement).returncode == 0
    with sqlite3.connect(output) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute("SELECT * FROM functions")]
    assert len(rows) == 2
    merged = next(row for row in rows if row["name"] == "PasGetLibProps")
    assert merged["source_kind"] == "pdf"
    assert merged["domain"] == "pdk_automation"
    assert merged["document"].endswith("#page=221")


def test_v2_json_fallback_matches_the_sqlite_store(tmp_path: Path, monkeypatch) -> None:
    source = _corpus(tmp_path)
    output = tmp_path / "reference" / "skill_api.sqlite3"
    assert _build(source, output).returncode == 0

    sqlite_reference = SkillReference(output.parent)
    expected_search = sqlite_reference.search({
        "query": "substring", "kind": "function", "domain": "skill_language",
    })
    expected_get = sqlite_reference.get({"name": "substring"})
    sqlite_reference.close()

    def unavailable():
        raise ModuleNotFoundError("forced missing sqlite3")

    monkeypatch.setattr(skill_reference_store, "_import_sqlite", unavailable)
    fallback = SkillReference(output.parent)
    assert fallback.search({
        "query": "substring", "kind": "function", "domain": "skill_language",
    }) == expected_search
    assert fallback.get({"name": "substring"}) == expected_get
    fallback.close()
