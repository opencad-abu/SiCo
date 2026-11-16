"""Training publications preserve source evidence and enforce asset/shard boundaries."""

import copy
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from test_template_library_tiers import record

from cadai.template_capture import RECORD_FIELDS
from cadai.template_catalog import TemplateCatalog
from cadai.template_schema import CAPTURE_SCHEMA, arguments

ROOT = Path(__file__).resolve().parents[2] / "reference/circuit_templates"
spec = importlib.util.spec_from_file_location(
    "training_builder", ROOT / "training/build_catalogs.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_shipped_training_manifest_hashes_coverage_and_query():
    reports = json.loads((ROOT / "training/SOURCES.json").read_text())
    expected = {"ams_emir": 18, "ams_gui": 76, "custom_ic": 634, "aot": 133, "dot": 204}
    assert {r["id"]: r["publication"]["templates"] for r in reports} == expected
    for source in reports:
        report = json.loads((ROOT / "training" / (source["id"] + ".json")).read_text())
        refs = set()
        for shard in source["publication"]["catalogs"]:
            path = ROOT / shard["catalog"]
            assert path.stat().st_size == shard["bytes"] <= 64 * 1024 * 1024
            assert hashlib.sha256(path.read_bytes()).hexdigest() == shard["sha256"]
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
                assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                summaries = [
                    json.loads(r[0]) for r in db.execute("SELECT summary_json FROM templates")
                ]
            assert len(summaries) == shard["templates"]
            assert not refs.intersection(s["template_ref"] for s in summaries)
            refs.update(s["template_ref"] for s in summaries)
        published = [t for t in report["templates"] if t["status"] == "published"]
        assert refs == {t["template_ref"] for t in published}
        assert source["assets"] == {
            asset: sum(asset in t["assets"] for t in published)
            for asset in ("schematic", "symbol", "layout")
        }
        assert report["source"]["cds_lib_sha256"]
    result = TemplateCatalog(ROOT).query(
        arguments("query_circuit_templates", dict(data_format="legacy"))
    )
    assert result["total"] >= 2 + sum(expected.values())


def test_sharding_keeps_fixed_references_and_refuses_overwrite(tmp_path, monkeypatch):
    records = [record("first"), record("second")]
    original = copy.deepcopy(records)
    monkeypatch.setattr(builder, "SHARD_PAYLOAD_BYTES", 1)
    result = builder.publish_dataset(records, tmp_path, "example")
    assert len(result["catalogs"]) == 2
    for item in original:
        assert TemplateCatalog(tmp_path).get(item["template_ref"]) == item
    assert records == original
    with pytest.raises(FileExistsError):
        builder.publish_dataset(records, tmp_path, "example")
    with pytest.raises(ValueError, match="duplicate"):
        builder.publish_dataset([records[0], records[0]], tmp_path / "duplicates", "example")


@pytest.mark.parametrize("fault", ["partial", "duplicate_json", "outside_asset"])
def test_splitter_never_repairs_invalid_source_framing(tmp_path, fault):
    row = {"kind": "missing_asset", "asset": "layout", "view": "layout", "reason": "view_not_found"}
    raw = builder.bundle(["layout"], [row])
    if fault == "partial":
        raw = b"\n".join(raw.splitlines()[:-1]) + b"\n"
    elif fault == "duplicate_json":
        raw = raw.replace(b'"reason":', b'"reason":"overwritten","reason":')
    else:
        raw = builder.bundle(["layout"], [{"kind": "cell_property", "name": "stray"}, row])
    path = tmp_path / "source.jsonl"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        builder.split_assets(path)


@pytest.mark.parametrize("fault", ["invalid_layout", "missing_layout"])
def test_failed_layout_preserves_valid_symbol_and_missing_evidence(tmp_path, fault):
    stage, output = tmp_path / "capture", tmp_path / "published"
    directory = stage / "ams_emir"
    directory.mkdir(parents=True)
    target = {
        "library": "test",
        "cell": "block",
        "schematic_view": "schematic",
        "symbol_view": "symbol",
        "layout_view": "layout",
        "primary": True,
        "assets": ["symbol", "layout"],
        "path": "block.jsonl",
    }
    (directory / "targets.json").write_text(json.dumps([target]))
    header = {
        "kind": "asset_header",
        "schema_version": CAPTURE_SCHEMA,
        "asset": "symbol",
        "lib": "test",
        "cell": "block",
        "view": "symbol",
        "library_path": "/source/test",
        "tool_version": "test",
        "status": "raw_capture",
    }
    footer = {
        "kind": "asset_footer",
        "asset": "symbol",
        "complete": True,
        "counts": dict.fromkeys(RECORD_FIELDS, 0),
    }
    rows = [header, footer]
    if fault == "invalid_layout":
        rows += [
            {**header, "asset": "layout", "view": "layout"},
            {**footer, "asset": "layout", "complete": False},
        ]
    else:
        rows += [
            {
                "kind": "missing_asset",
                "asset": "layout",
                "view": "layout",
                "reason": "view_not_found",
            }
        ]
    raw = builder.bundle(target["assets"], rows)
    (directory / "block.jsonl").write_bytes(raw)
    result = builder.build_dataset(
        stage,
        {
            "id": "ams_emir",
            "cds_lib": "/source/cds.lib",
            "cds_lib_sha256": "a" * 64,
            "resolved_libraries": {"test": "/source/test"},
        },
        output,
    )
    assert result["publication"]["templates"] == 1 and result["assets"] == {"symbol": 1}
    catalog = TemplateCatalog(output)
    ref = catalog.query(arguments("query_circuit_templates", {}))["templates"][0]["template_ref"]
    item = catalog.get(ref)
    assert set(item["assets"]) == {"symbol"}
    assert item["missing_assets"][0]["asset"] == "layout"
    assert item["provenance"]["raw_capture_sha256"] == {
        "ams_emir/block.jsonl": hashlib.sha256(raw).hexdigest()
    }
    assert (directory / "block.jsonl").read_bytes() == raw
