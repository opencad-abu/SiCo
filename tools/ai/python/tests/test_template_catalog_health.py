"""Catalog health must detect a capture whose published symbol was dropped."""

import hashlib
import json
import sqlite3

from template_qualification_fixture import symbol_rows

from cadai.template_build import build_manifest
from cadai.template_catalog_health import health
from cadai.template_schema import canonical


def make_catalog(tmp_path):
    rows = symbol_rows(schematic=True)
    capture = tmp_path / "capture.jsonl"
    raw = ("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n").encode()
    capture.write_bytes(raw)
    manifest = {
        "schema_version": 1,
        "pin_policy": {"inputOutput": "upper_left", "input": "lower_left", "output": "right"},
        "sources": {"demo": {"cds_lib": "/reference/demo/cds.lib"}},
        "captures": [
            {
                "source": "demo",
                "path": capture.name,
                "category": "digital",
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    catalog = tmp_path / "catalog.sqlite3"
    build_manifest(manifest_path, catalog)
    return manifest_path, catalog


def test_health_accepts_complete_capture_and_symbol(tmp_path):
    manifest, catalog = make_catalog(tmp_path)
    report = health(manifest, catalog)
    assert report["ok"] is True
    assert report["captures"] == 1 and report["matched"] == 1
    assert report["rows"][0]["catalog_assets"] == ["schematic", "symbol"]


def test_health_reports_capture_symbol_dropped_from_catalog(tmp_path):
    manifest, catalog = make_catalog(tmp_path)
    with sqlite3.connect(catalog) as database:
        ref, data = database.execute("SELECT ref, data_json FROM templates").fetchone()
        record = json.loads(data)
        record["assets"].pop("symbol")
        record["summary"]["assets"] = [
            asset for asset in record["summary"]["assets"] if asset != "symbol"
        ]
        database.execute(
            "UPDATE templates SET data_json=?, summary_json=? WHERE ref=?",
            (canonical(record), canonical(record["summary"]), ref),
        )
    report = health(manifest, catalog)
    assert report["ok"] is False
    assert "missing_asset:symbol" in report["errors"][0]
    assert report["rows"][0]["errors"] == ["missing_asset:symbol"]
