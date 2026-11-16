"""Migration preserves native catalog bytes, provenance, and capture identities."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from cadai.template_build import make_template, publish
from cadai.template_capture import parse_capture
from cadai.template_storage import TemplateStorage
from sico.migration.audit import audit
from sico.migration.prepare import prepare


@pytest.fixture(autouse=True)
def stopped_processes(monkeypatch):
    observe = lambda source: {"scope": "local", "referencing_pids": [], "unreadable_processes": 0}
    monkeypatch.setattr("sico.migration.audit.scan_local", observe)
    monkeypatch.setattr("sico.migration.prepare.scan_local", observe)


def catalog(project):
    root = project / ".cad/ai/circuit_templates"
    for path in (project / ".cad", project / ".cad/ai", root,
                 root / "captures", root / "catalogs"):
        path.mkdir(mode=0o700)
    rows = [
        {"kind": "header", "schema_version": 1, "status": "raw_capture",
         "lib": "user", "cell": "empty", "view": "schematic",
         "library_path": str(project / ".cad/historical-library"),
         "tool_version": "test", "dbu_per_uu": 100},
        {"kind": "footer", "complete": True, "counts": {
            key: 0 for key in ("instance", "instance_terminal", "net", "terminal", "pin", "shape")}},
    ]
    raw = ("\n".join(json.dumps(row) for row in rows) + "\n").encode()
    digest = hashlib.sha256(raw).hexdigest()
    capture = root / "captures" / (digest + ".jsonl")
    capture.write_bytes(raw)
    capture.chmod(0o600)
    record = make_template(parse_capture(raw))
    shard = root / "catalogs" / (record["template_ref"] + ".sqlite3")
    publish([record], shard)
    return root, capture, shard, record


def test_catalog_and_capture_are_copied_verbatim_and_remain_readable(tmp_path):
    root, capture, shard, record = catalog(tmp_path)
    report = audit(tmp_path)
    assert report["blockers"] == []
    before = {path: path.read_bytes() for path in (capture, shard)}
    result = prepare(tmp_path, expected_inventory=report["inventory_sha256"], writers_stopped=True)
    staged = Path(result["transaction"]) / "staged/ai/circuit_templates"
    for original, data in before.items():
        assert original.read_bytes() == data
        assert (staged / original.relative_to(root)).read_bytes() == data
    assert TemplateStorage(staged).locate(record["template_ref"])[0] == record
    assert not (tmp_path / ".sico").exists()


@pytest.mark.parametrize("damage", ["sqlite", "capture", "missing_capture", "missing_captures",
                                    "summary", "reference", "trigger", "index", "columns"])
def test_invalid_catalog_or_cross_reference_blocks_without_repair(tmp_path, damage):
    _root, capture, shard, record = catalog(tmp_path)
    if damage == "sqlite":
        shard.write_bytes(b"invalid SQLite")
    elif damage == "capture":
        capture.write_bytes(capture.read_bytes() + b" ")
    elif damage in {"missing_capture", "missing_captures"}:
        capture.unlink()
        if damage == "missing_captures":
            capture.parent.rmdir()
    else:
        with sqlite3.connect(str(shard)) as db:
            if damage == "summary":
                summary = dict(record["summary"], cell="changed")
                db.execute("UPDATE templates SET summary_json=?", (json.dumps(summary),))
            elif damage == "trigger":
                db.execute("CREATE TRIGGER unexpected AFTER INSERT ON metadata BEGIN "
                           "DELETE FROM templates; END")
            elif damage == "index":
                db.execute("DROP INDEX lookup")
                db.execute("CREATE INDEX lookup ON templates(search_text)")
            elif damage == "columns":
                db.execute("ALTER TABLE templates ADD COLUMN unexpected TEXT")
            else:
                db.execute("UPDATE templates SET ref=?", ("tpl_" + "0" * 64,))
    before = shard.read_bytes()
    report = audit(tmp_path)
    assert "invalid_or_changed_record" in {row["code"] for row in report["blockers"]}
    assert shard.read_bytes() == before
    assert not (tmp_path / ".sico-migration").exists()


def test_sqlite_sidecars_and_unregistered_template_files_block(tmp_path):
    _root, _capture, shard, _record = catalog(tmp_path)
    sidecar = shard.with_name(shard.name + "-wal")
    sidecar.write_bytes(b"uncheckpointed")
    sidecar.chmod(0o600)
    assert "unregistered_file" in {row["code"] for row in audit(tmp_path)["blockers"]}
    assert sidecar.read_bytes() == b"uncheckpointed"


def test_preview_bytes_are_retained_as_historical_evidence(tmp_path):
    root, _capture, _shard, record = catalog(tmp_path)
    (root / "previews").mkdir(mode=0o700)
    directory = root / "previews" / (record["template_ref"] + "-preview4")
    directory.mkdir(mode=0o700)
    preview = directory / "index.html"
    preview.write_text("<p>Historical preview</p>")
    preview.chmod(0o600)
    report = audit(tmp_path)
    assert report["blockers"] == []
    entry = next(row for row in report["entries"] if row["path"].endswith("index.html"))
    assert entry["action"] == "retain"
    assert entry["sha256"] == hashlib.sha256(preview.read_bytes()).hexdigest()
    directory.rename(directory.with_name("tpl_" + "f" * 64 + "-preview4"))
    assert "missing_preview_template" in {row["code"] for row in audit(tmp_path)["blockers"]}
