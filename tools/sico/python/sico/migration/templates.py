"""Validate immutable user catalogs without editing SQLite or historical provenance."""

import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath

from cadai.template_capture import parse_capture
from cadai.template_schema import MAX_CAPTURE_BYTES
from cadai.template_validation import catalog_references


def validate_template(stream, entry, inventory):
    if entry["schema"] == "template_capture":
        capture = parse_capture(stream.read(MAX_CAPTURE_BYTES + 1))
        if capture["sha256"] != PurePosixPath(entry["path"]).stem:
            raise ValueError("Template capture identity differs from filename")
        return
    # A private snapshot lets the existing SQLite reader operate without opening
    # source sidecars or following a pathname replaced after descriptor validation.
    with tempfile.TemporaryDirectory(prefix="sico-migration-catalog-", dir="/tmp") as temporary:
        path = Path(temporary) / "catalog.sqlite3"
        remaining = entry["identity"]["size"]
        digest = hashlib.sha256()
        with path.open("xb") as output:
            os.chmod(path, 0o600)
            while remaining:
                chunk = stream.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise ValueError("Template catalog changed during validation")
                remaining -= len(chunk)
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != entry["sha256"]:
            raise ValueError("Template catalog hash changed")
        references = catalog_references(path)
        for record in references:
            validate_reference(entry["path"], record, inventory)
        entry["template_references"] = references


def validate_reference(relative, record, inventory):
    path = PurePosixPath(relative)
    shard = path.parent.name == "catalogs"
    if shard and path.stem != record["template_ref"]:
        raise ValueError("Template shard reference differs from filename")
    root = path.parent.parent if shard else path.parent
    capture_hash = record["capture_sha256"]
    capture = inventory.get(str(root / "captures" / (capture_hash + ".jsonl")))
    if capture is not None and (capture.get("sha256") != capture_hash
                                or capture.get("schema") != "template_capture"):
        raise ValueError("Template capture reference differs from retained bytes")
    if shard and capture is None:
        raise ValueError("Private template shard lost its retained capture")


def reference_conflicts(rows):
    references, roots, conflicts = {}, {}, []
    for row in rows:
        for reference in row.get("template_references", []):
            path = PurePosixPath(row["path"])
            root = path.parent.parent if path.parent.name == "catalogs" else path.parent
            key = reference["template_ref"]
            roots.setdefault(root, set()).add(key)
            previous = references.setdefault(key, reference["sha256"])
            if previous != reference["sha256"]:
                conflicts.append({"path": row["path"], "code": "template_reference_conflict"})
    for row in rows:
        if row["schema"] == "template_preview_directory":
            path = PurePosixPath(row["path"])
            if path.name.removesuffix("-preview4") not in roots.get(path.parent.parent, set()):
                conflicts.append({"path": row["path"], "code": "missing_preview_template"})
    return conflicts
