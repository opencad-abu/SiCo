"""Validate sealed router history without reviving discovery or changing evidence."""

import gzip
import hashlib
import json
import os
import zlib
from contextlib import contextmanager

from ..core.contracts import identifier
from ..transport.bridge_identity import BRIDGE_PROTOCOL
from ..transport.router_archive_records import (
    FILES, IDENTITY, requires_reconciliation, validate_archived_stream,
    validate_manifest, validate_retirement,
)
from ..transport.router_journal import load_records
from .filesystem import MAX_FILE_BYTES, fingerprint, issue
from .records import json_record


@contextmanager
def checked_stream(tree, entry):
    with tree.open(entry["path"]) as stream:
        if fingerprint(os.fstat(stream.fileno())) != entry["identity"]:
            raise ValueError("Router evidence changed")
        yield stream
        if fingerprint(os.fstat(stream.fileno())) != entry["identity"]:
            raise ValueError("Router evidence changed")


def metadata(tree, entry):
    with checked_stream(tree, entry) as stream:
        return json_record(stream, 65536)


def source_path(prefix, name):
    return prefix + (".state/" if name == "requests.jsonl" else ".") + name


def sealed_stamp(entry):
    identity = entry["identity"]
    return [identity[key] for key in ("device", "inode", "size", "mtime_ns", "ctime_ns")]


def validate_generation(tree, prefix, inventory):
    descriptor = metadata(tree, inventory[prefix + ".json"])
    if descriptor.get("protocol") != BRIDGE_PROTOCOL:
        raise ValueError("Invalid bridge descriptor")
    identity = {key: identifier(descriptor[key]) for key in IDENTITY}
    seal = validate_retirement(metadata(tree, inventory[prefix + ".retired.json"]), identity)
    key = json.dumps([seal["hostname"], identity["instance_id"], identity["generation"]])
    if prefix.rsplit("/", 1)[1] != hashlib.sha256(key.encode()).hexdigest():
        raise ValueError("Bridge discovery key differs from sealed identity")
    for suffix in (".lock", ".state/writer.lock", ".state/maintenance.lock"):
        if inventory[prefix + suffix]["schema"] != "router_lock":
            raise ValueError("Missing router ownership lock")
    for name in FILES:
        entry = inventory.get(source_path(prefix, name))
        if entry is not None and seal["files"].get(name) != sealed_stamp(entry):
            raise ValueError("Router source differs from retirement seal")
    archive = prefix + ".state/archive/"
    manifest_entry = inventory.get(archive + "manifest.json")
    if manifest_entry is None:
        if any(path.startswith(archive) for path in inventory):
            raise ValueError("Incomplete router archive")
        if any(source_path(prefix, name) not in inventory for name in seal["files"]):
            raise ValueError("Sealed source is missing")
        entry = inventory[source_path(prefix, "requests.jsonl")]
        with checked_stream(tree, entry) as stream:
            rows, _, offset = load_records(stream, identity)
        if offset != entry["identity"]["size"]:
            raise ValueError("Router source has an incomplete tail")
    else:
        manifest = validate_manifest(metadata(tree, manifest_entry), identity)
        if (set(manifest["files"]) != set(seal["files"])
                or manifest.get("retired_at") != seal["retired_at"]):
            raise ValueError("Archive inventory differs from retirement seal")
        expected, rows = {archive + "manifest.json"}, {}
        for name, item in manifest["files"].items():
            if item["size"] != seal["files"][name][2] or item["size"] > MAX_FILE_BYTES:
                raise ValueError("Archive size differs from sealed evidence or exceeds limit")
            source = inventory.get(source_path(prefix, name))
            if source is not None and (source["sha256"] != item["sha256"]
                                       or item["status"] == "pruned"):
                raise ValueError("Archive content differs from remaining sealed source")
            if item["status"] == "pruned":
                continue
            path = archive + name + ".gz"
            expected.add(path)
            with checked_stream(tree, inventory[path]) as raw:
                with gzip.GzipFile(fileobj=raw, mode="rb") as stream:
                    evidence = validate_archived_stream(stream, name, item, identity,
                                                        require_complete=True)
            if name == "requests.jsonl":
                rows = evidence
        actual = {path for path, entry in inventory.items()
                  if path.startswith(archive) and entry["kind"] == "file"}
        if actual != expected:
            raise ValueError("Archive has extra or missing evidence")
    for row in rows.values():
        if (any(row.get(key) != value for key, value in identity.items())
                or row.get("automatic_resume_allowed") is not False):
            raise ValueError("Router receipt identity mismatch")
        identifier(row["session_id"])
        identifier(row["target_id"])
    if requires_reconciliation(rows):
        raise ValueError("Router history requires reconciliation before migration")


def validate_routers(tree, rows):
    inventory = {row["path"]: row for row in rows}
    prefixes = {"/".join(row["path"].split("/")[:3]) + "/" +
                row["path"].split("/")[3].split(".")[0]
                for row in rows if row["schema"].startswith("router_")}
    blockers = []
    for prefix in sorted(prefixes):
        try:
            validate_generation(tree, prefix, inventory)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, EOFError,
                RecursionError, zlib.error):
            blockers.append(issue(prefix, "invalid_or_unresolved_router_history"))
    return blockers
