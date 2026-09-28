"""Sealed-router metadata integrity and read-only archive streams."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import stat
import uuid
import zlib
from contextlib import contextmanager

from ..storage.journal import open_private, sync_directory
from .framing import strict_json
from .router_journal import load_records, record_digest

PROTOCOL = "cad_ai_router_archive.v1"

SEAL_PROTOCOL = "cad_ai_router_retired.v1"

IDENTITY = ("instance_id", "generation", "bridge_id", "router_id")

DIAGNOSTICS = ("router.jsonl", "relay.jsonl", "skill.jsonl")

PRESERVED = ("requests.jsonl", "bindings.jsonl")

FILES = PRESERVED + DIAGNOSTICS

def _directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Expected a private owned archive directory")

def _json(path):
    with os.fdopen(open_private(path, os.O_RDONLY), "rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("Archive metadata exceeds limit")
    record = strict_json(data)
    if not isinstance(record, dict):
        raise ValueError("Archive metadata must be an object")
    return record

def _atomic_json(path, record):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
    try:
        with os.fdopen(
            open_private(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY), "w"
        ) as stream:
            json.dump(record, stream, ensure_ascii=True, allow_nan=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)

def _source(path, name):
    return (
        path.with_suffix(".state") / name
        if name == "requests.jsonl"
        else path.with_suffix("." + name)
    )

def _stamp(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]

def _manifest(directory, identity):
    _directory(directory)
    return validate_manifest(_json(directory / "manifest.json"), identity)


def validate_manifest(record, identity):
    """Validate archive metadata without depending on its storage pathname."""
    manifest = dict(record)
    checksum = manifest.pop("sha256", None)
    if (
        checksum != record_digest(manifest)
        or manifest.get("protocol") != PROTOCOL
        or manifest.get("identity") != identity
    ):
        raise ValueError("Archive manifest identity or checksum mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict) or "requests.jsonl" not in files or set(files) - set(FILES):
        raise ValueError("Invalid archive inventory")
    for name, item in files.items():
        if (
            not isinstance(item, dict)
            or type(item.get("size")) is not int
            or item["size"] < 0
            or not re.fullmatch("[0-9a-f]{64}", str(item.get("sha256", "")))
            or item.get("status") not in {"retained", "pruned"}
            or (name in PRESERVED and item["status"] != "retained")
        ):
            raise ValueError("Invalid archive file metadata")
    return manifest


def validate_retirement(record, identity, *, hostname=None):
    """Validate the immutable seal; live maintenance also requires its local host."""
    seal = dict(record)
    checksum = seal.pop("sha256", None)
    if (
        checksum != record_digest(seal)
        or seal.get("protocol") != SEAL_PROTOCOL
        or seal.get("identity") != identity
        or not isinstance(seal.get("hostname"), str)
        or not seal["hostname"]
        or hostname is not None and seal["hostname"] != hostname
        or type(seal.get("retired_at")) not in {float, int}
        or not isinstance(seal.get("files"), dict)
        or "requests.jsonl" not in seal["files"]
        or set(seal["files"]) - set(FILES)
    ):
        raise ValueError("Invalid retirement seal")
    for stamp in seal["files"].values():
        if (not isinstance(stamp, list) or len(stamp) != 5
                or any(type(value) is not int or value < 0 for value in stamp)):
            raise ValueError("Invalid sealed file identity")
    return seal


def requires_reconciliation(rows):
    """A transport completion cannot reconcile a circuit or Assistant write claim."""
    from .methods import READ_METHODS

    safe = {"completed", "failed", "cancelled_before_start", "queue_timeout",
            "queue_full", "blocked_unknown"}
    return any(row.get("state") not in safe or row.get("method") not in READ_METHODS
               or row.get("started_at") and not isinstance(row.get("reply"), dict)
               for row in rows.values())

def _save_manifest(directory, manifest):
    _atomic_json(directory / "manifest.json", dict(manifest, sha256=record_digest(manifest)))

def _verify_stream(stream, item):
    size, digest = 0, hashlib.sha256()
    while True:
        chunk = stream.read(min(1024 * 1024, item["size"] - size + 1))
        if not chunk:
            break
        size += len(chunk)
        if size > item["size"]:
            raise ValueError("Archive file exceeds recorded size")
        digest.update(chunk)
    if size != item["size"] or digest.hexdigest() != item["sha256"]:
        raise ValueError("Archive file checksum mismatch")


def validate_archived_stream(stream, name, item, identity, *, require_complete=False):
    """Check original bytes, allowing historical partial tails only for live readers."""
    if name == "requests.jsonl":
        rows, _, offset = load_records(stream, identity, item)
        if require_complete and offset != item["size"]:
            raise ValueError("Router journal requires reconciliation of its partial tail")
        return rows
    _verify_stream(stream, item)
    return {}

@contextmanager
def _compressed(path):
    try:
        with os.fdopen(open_private(path, os.O_RDONLY), "rb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="rb") as stream:
                yield stream
    except (zlib.error, EOFError) as error:
        raise ValueError("Corrupt compressed router evidence") from error

@contextmanager
def archived_journal(directory, identity):
    archive = directory / "archive"
    manifest = _manifest(archive, identity)
    with _compressed(archive / "requests.jsonl.gz") as stream:
        yield stream, manifest["files"]["requests.jsonl"]

def _validate_archive(directory, identity):
    manifest = _manifest(directory, identity)
    rows = {}
    for name, item in manifest["files"].items():
        if item["status"] == "pruned":
            continue
        with _compressed(directory / (name + ".gz")) as stream:
            evidence = validate_archived_stream(stream, name, item, identity)
            if name == "requests.jsonl":
                rows = evidence
    return manifest, rows
