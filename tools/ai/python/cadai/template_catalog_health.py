"""Audit retained capture coverage against one published legacy catalog.

This is a maintenance entry, not a runtime tool.  It re-reads each capture in
the manifest, verifies its digest, and compares the captured asset set with the
catalog record that was published for that capture.  A capture that contains a
symbol but whose catalog record does not is reported as stale/incomplete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from .template_capture import read_capture
from .template_schema import TemplateError, canonical
from .template_storage import catalog_json


def _records(path):
    database = Path(path)
    if not database.is_file():
        raise TemplateError("catalog is missing: " + str(database))
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return [
            (ref, catalog_json(data))
            for ref, data in connection.execute("SELECT ref, data_json FROM templates")
        ]
    finally:
        connection.close()


def _capture_rows(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != 1:
        raise TemplateError("unsupported raw capture manifest")
    rows = []
    for entry in manifest.get("captures", []):
        capture_path = (manifest_path.parent / entry["path"]).resolve()
        if manifest_path.parent not in capture_path.parents:
            raise TemplateError("capture path escapes manifest directory")
        raw = capture_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != entry.get("sha256"):
            raise TemplateError("capture digest mismatch: " + str(capture_path))
        capture = read_capture(capture_path)
        header = next(iter(capture["assets"].values()))["header"]
        rows.append(
            {
                "source": entry.get("source"),
                "category": entry.get("category"),
                "path": str(capture_path),
                "sha256": digest,
                "library": header.get("lib"),
                "cell": header.get("cell"),
                "assets": sorted(capture["assets"]),
                "missing": sorted(row["asset"] for row in capture["missing"]),
                "capture": capture,
            }
        )
    return rows


def health(manifest_path, catalog_path):
    """Return a deterministic coverage report; no catalog or capture is modified."""
    captures = _capture_rows(manifest_path)
    records = _records(catalog_path)
    by_digest = {}
    for ref, record in records:
        by_digest.setdefault(record.get("capture_sha256"), []).append((ref, record))

    errors, warnings, rows = [], [], []
    seen = set()
    for item in captures:
        candidates = by_digest.get(item["sha256"], [])
        candidates = [
            (ref, record)
            for ref, record in candidates
            if record.get("source", {}).get("lib") == item["library"]
            and record.get("source", {}).get("cell") == item["cell"]
        ]
        if not candidates:
            errors.append(
                f"capture has no catalog record: {item['library']}/{item['cell']} "
                f"({item['sha256']})"
            )
            rows.append(
                {key: value for key, value in item.items() if key != "capture"}
                | {"template_ref": None, "catalog_assets": []}
            )
            continue
        if len(candidates) > 1:
            warnings.append(
                f"capture has multiple catalog records: {item['library']}/{item['cell']} "
                f"({item['sha256']})"
            )
        ref, record = candidates[0]
        seen.add(ref)
        actual = set(record.get("assets", {}))
        summary_assets = set(record.get("summary", {}).get("assets", []))
        expected = set(item["assets"])
        row_errors = []
        if actual != summary_assets:
            row_errors.append("summary_assets_mismatch")
        for name in sorted(expected - actual):
            row_errors.append("missing_asset:" + name)
        for name in sorted(actual - expected):
            row_errors.append("extra_asset:" + name)
        for name in sorted(set(item["missing"]) - set(record.get("missing_assets", []))):
            row_errors.append("missing_asset_not_recorded:" + name)
        symbol = record.get("assets", {}).get("symbol")
        if "symbol" in expected and symbol:
            if not symbol.get("shapes") or not symbol.get("pins") or not symbol.get("ports"):
                row_errors.append("symbol_geometry_empty")
        if record.get("capture_sha256") != item["sha256"]:
            row_errors.append("capture_digest_mismatch")
        errors.extend(f"{item['library']}/{item['cell']}: {code}" for code in row_errors)
        rows.append(
            {
                "template_ref": ref,
                "library": item["library"],
                "cell": item["cell"],
                "capture_sha256": item["sha256"],
                "expected_assets": item["assets"],
                "catalog_assets": sorted(actual),
                "summary_assets": sorted(summary_assets),
                "errors": row_errors,
            }
        )

    orphan = [
        ref
        for ref, record in records
        if ref not in seen and record.get("capture_sha256") not in {c["sha256"] for c in captures}
    ]
    if orphan:
        warnings.append(f"catalog records without a manifest capture: {len(orphan)}")

    return {
        "ok": not errors,
        "manifest": str(Path(manifest_path).resolve()),
        "catalog": str(Path(catalog_path).resolve()),
        "captures": len(captures),
        "records": len(records),
        "matched": sum(1 for row in rows if row["template_ref"]),
        "errors": errors,
        "warnings": warnings,
        "orphan_refs": orphan,
        "rows": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = health(args.manifest, args.catalog)
    except (OSError, ValueError, TemplateError) as exc:
        print(canonical({"ok": False, "error": str(exc)}))
        return 2
    print(canonical(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
