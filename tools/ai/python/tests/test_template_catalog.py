"""Reference imports must reject partial/corrupt captures and preserve originals."""
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

MODULE = Path(__file__).resolve().parents[2] / "reference/schematic-templates/build_catalog.py"
spec = importlib.util.spec_from_file_location("template_catalog", MODULE)
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


def capture_rows():
    rows = [
        {"kind": "header", "schema_version": 1, "status": "raw_capture",
         "lib": "example", "cell": "inv", "view": "schematic",
         "library_path": "/reference/example", "tool_version": "test"},
        {"kind": "instance", "name": "M0", "libName": "pdk", "cellName": "nmos",
         "viewName": "symbol", "master_available": False},
        {"kind": "net", "name": "out", "instance_terminal_count": 1},
        {"kind": "instance_terminal", "instance": "M0", "name": "D", "net": "out"},
        {"kind": "terminal", "name": "Y", "net": "out", "direction": "output"},
        {"kind": "pin", "terminal": "Y", "net": "out"},
    ]
    counts = Counter(r["kind"] for r in rows)
    rows.append({"kind": "footer", "complete": True,
                 "counts": {kind: counts[kind] for kind in catalog.COUNTED}})
    return rows


def write_capture(tmp_path, rows):
    path = tmp_path / "capture.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


@pytest.mark.parametrize("fault", ["partial", "count", "net", "instance", "pin", "duplicate", "schema"])
def test_reject_invalid_capture(tmp_path, fault):
    rows = capture_rows()
    if fault == "partial":
        rows.pop()
    elif fault == "count":
        rows[-1]["counts"]["shape"] = 100
    elif fault == "net":
        rows[3]["net"] = "missing"
    elif fault == "instance":
        rows[3]["instance"] = "missing"
    elif fault == "pin":
        rows[5]["terminal"] = "missing"
    elif fault == "duplicate":
        rows.insert(4, dict(rows[3]))
        rows[-1]["counts"]["instance_terminal"] += 1
    elif fault == "schema":
        rows[0]["schema_version"] = 999
    with pytest.raises(ValueError):
        catalog.load_capture(write_capture(tmp_path, rows))


def make_manifest(tmp_path):
    path = write_capture(tmp_path, capture_rows())
    manifest = {
        "schema_version": 1, "pin_policy": catalog.PIN_POLICY,
        "sources": {"example": {"cds_lib": "/reference/cds.lib"}},
        "captures": [{"source": "example", "path": path.name, "category": "digital",
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return manifest_path, path


def test_catalog_retains_incomplete_master_and_refuses_overwrite(tmp_path):
    manifest, capture = make_manifest(tmp_path)
    original = capture.read_bytes()
    output = tmp_path / "catalog.sqlite3"
    assert catalog.build(manifest, output)["templates"] == 1
    with sqlite3.connect(str(output)) as connection:
        status, issues = connection.execute("SELECT status, observations_json FROM templates").fetchone()
        assert status == "raw_capture"
        assert json.loads(issues)["missing_masters"] == ["pdk/nmos/symbol"]
        assert connection.execute("SELECT count(*) FROM records").fetchone()[0] == 5
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        catalog.build(manifest, output)
    assert hashlib.sha256(output.read_bytes()).hexdigest() == digest
    assert capture.read_bytes() == original


def test_bad_digest_does_not_publish_catalog(tmp_path):
    manifest, capture = make_manifest(tmp_path)
    capture.write_bytes(capture.read_bytes() + b"\n")
    # Whitespace-only changes retain valid JSON records but change the digest.
    capture.write_bytes(capture.read_bytes().replace(b'"kind"', b'"kind" '))
    capture.write_bytes(capture.read_bytes().rstrip() + b"\n")
    with pytest.raises(ValueError, match="digest mismatch"):
        catalog.build(manifest, tmp_path / "catalog.sqlite3")
    assert not (tmp_path / "catalog.sqlite3").exists()
