"""Derived bounded query rows for large verified standard roots.

The standard allows an index derived from verified JSON as long as the
authoritative records stay untouched and the algorithm version is registered.
A row is exactly the query projection (template entry plus summary facts);
detail, matching and replay still read and verify the package itself.  Every
reuse re-verifies the recorded manifest and content digests, so a rebuilt file
is never trusted over changed bytes.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

from .codec import fail, sha
from .topology import graph

ALGORITHM = "sico-standard-query-index-v1"
FILE = ".derived-query-v1.json"
RESPONSE_FIELDS = (
    "template_ref",
    "name",
    "description",
    "categories",
    "detail_level",
    "use",
    "library",
    "cell",
    "counts",
    "capabilities",
)


def response(entry):
    """The bounded query row shape callers already receive."""
    return {key: copy.deepcopy(entry[key]) for key in RESPONSE_FIELDS} | {
        "origin": copy.deepcopy(entry["origin"]),
        "available_from": copy.deepcopy(entry["origins"]),
    }


def row(item):
    """The bounded query projection of one verified template."""
    entry, docs = item["entry"], item["documents"]
    top = docs.get("topology", {})
    origin = docs["collection"].get("origin", {})
    return {
        "template_ref": entry["template_ref"],
        "name": entry["name"],
        "description": entry["description"],
        "categories": entry["categories"],
        "detail_level": entry["detail_level"],
        "use": entry["use"],
        "library": origin.get("library", "unknown"),
        "cell": origin.get("cell", entry["name"]),
        "counts": {k: len(top.get(k, {})) for k in ("devices", "nets", "ports")},
        "capabilities": {
            k: r["result"] for k, r in docs["qualification"]["checks"].items()
        },
        "assets": sorted(docs["collection"]["assets"]),
        "device_kinds": sorted({d["kind"] for d in top.get("devices", {}).values()}),
        "topology_fingerprint": graph(top)["fingerprint"] if top else None,
        "origin": item["origin"],
        "origins": item["origins"],
        # Locators let a caller load exactly the one package it asked for.
        "package": str(item["path"]),
        "template_id": item["template_id"],
    } | ({"overlay_base": str(item["overlay_base"])} if "overlay_base" in item else {})


def stamps(root, observed):
    """Size and mtime per recorded file; a writer always moves one of them."""
    result = {}
    for name in sorted(observed):
        try:
            stat = (Path(root) / name).stat()
        except OSError:
            return None
        result[name] = [stat.st_size, stat.st_mtime_ns]
    return result


def document(root, items, roots, members):
    """One derived document for exactly one installed root."""
    belonging = {Path(path) for path, _ in members if Path(root) in Path(path).parents}
    selected = {
        ref: row(item) for ref, item in items.items() if Path(item["path"]) in belonging
    }
    if not selected:
        return None
    observed = {}
    for index_root, files in roots:
        if Path(index_root) == Path(root):
            observed.update(files)
    for package_root, files in members:
        if Path(package_root) == Path(root):
            continue
        if Path(root) in Path(package_root).parents:
            prefix = str(Path(package_root).relative_to(root))
            observed.update({prefix + "/" + name: digest for name, digest in files.items()})
    if not observed:
        return None
    recorded = stamps(root, observed)
    if recorded is None:
        return None
    # Origins depend on how the root is mounted, so the file stores rows without
    # them and every read re-labels them from the configured location.
    stored = {
        ref: {key: value for key, value in row.items() if key not in ("origin", "origins")}
        for ref, row in selected.items()
    }
    return dict(
        algorithm=ALGORITHM,
        roots={str(root): {"observed": dict(sorted(observed.items())), "stamps": recorded}},
        rows=stored,
    )


def current(root):
    """Re-verify manifests by digest and every other file by size and mtime.

    The manifest set (index and package.json) carries every content digest, so
    hashing it is a strong check; the remaining files are compared by stamp, and
    any mismatch falls back to a fully verified rebuild.
    """
    path = root / FILE
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("algorithm") != ALGORITHM:
        return None
    entry = value.get("roots", {}).get(str(root), {})
    recorded, recorded_stamps = entry.get("observed"), entry.get("stamps")
    if not isinstance(recorded, dict) or not recorded:
        return None
    if not isinstance(recorded_stamps, dict) or not recorded_stamps:
        return None
    for name, digest in recorded.items():
        if not (name == "index.json" or name.endswith("package.json")):
            continue
        try:
            if sha((root / name).read_bytes()) != digest:
                return None
        except OSError:
            return None
    for name, stamp in recorded_stamps.items():
        try:
            stat = (root / name).stat()
        except OSError:
            return None
        if [stat.st_size, stat.st_mtime_ns] != stamp:
            return None
    return value


def persist(root, value):
    """Best-effort atomic write; a read-only root still serves rows in memory."""
    try:
        handle, temporary = tempfile.mkstemp(prefix=FILE + ".", dir=root)
    except OSError:
        return False
    try:
        with os.fdopen(handle, "w") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / FILE)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return False
    return True


def rows(catalog, *, rebuild=False):
    """Merged query rows and their fixed revision, or ``(None, None)``."""
    merged, present = {}, False
    for location in catalog.locations:
        root = location.path
        if not (root / "index.json").exists():
            continue
        value = None if rebuild else current(root)
        if value is None:
            from .reader import base_resolver, read_location

            located, observed, members = read_location(location, base_resolver(catalog))
            value = document(root, located, [(root, observed)], members)
            if value is None:
                return None, None
            persist(root, value)
        present = True
        for ref, stored in value["rows"].items():
            row = dict(stored, origin=location.describe(), origins=[location.describe()])
            existing = merged.get(ref)
            if existing is None:
                merged[ref] = copy.deepcopy(row)
                continue
            if existing["capabilities"] != row["capabilities"]:
                fail("conflicting qualification receipts for fixed reference")
            for origin in row["origins"]:
                if origin not in existing["origins"]:
                    existing["origins"].append(origin)
    if not present:
        return None, None
    from .reader import catalog_revision

    return merged, catalog_revision(catalog)
