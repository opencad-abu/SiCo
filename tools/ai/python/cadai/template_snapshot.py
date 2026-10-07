"""Read-only content fingerprints for directory template packages.

Snapshots are request-local observations, not published versions. Package
membership comes only from the bounded catalog scan; no manifest or policy
service is needed. A changing file is rejected rather than mixed into a read.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .template_schema import TemplateUnavailable, digest

SNAPSHOT_SCHEMA = "cad.template.snapshot.v2"
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_TOTAL_MEMBER_BYTES = 2 * 1024 * 1024 * 1024


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _member_stat(path):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_MEMBER_BYTES:
            raise TemplateUnavailable("catalog member is not a bounded regular file: " + str(path))
        # Packages contain complete SQLite files, never a live database whose
        # committed data depends on files absent from the content fingerprint.
        if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")):
            raise TemplateUnavailable("catalog member has a live SQLite journal: " + str(path))
        return info
    except OSError as exc:
        raise TemplateUnavailable("catalog member unavailable: " + str(path)) from exc


def verify_member(member, *, content=False):
    path = member["path"]
    if _signature(_member_stat(path)) != member["signature"]:
        raise TemplateUnavailable("catalog member changed during read; retry: " + str(path))
    # Same-size updates may share timestamp granularity. File metadata is a
    # fast rejection, not proof that the bytes still match this observation.
    # Rehash once at the public-read boundary, not for every row in a shard.
    if content:
        current = _file_member(path)
        if any(current[key] != member[key] for key in ("signature", "sha256")):
            raise TemplateUnavailable(
                "catalog member content changed during read; retry: " + str(path)
            )


def _file_member(path):
    before = _member_stat(path)
    sha, total = hashlib.sha256(), 0
    try:
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), "rb") as handle:
            if _signature(os.fstat(handle.fileno())) != _signature(before):
                raise TemplateUnavailable("catalog member changed before hashing: " + str(path))
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_MEMBER_BYTES:
                    raise TemplateUnavailable("catalog member exceeds size limit: " + str(path))
                sha.update(block)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise TemplateUnavailable("cannot hash catalog member: " + str(path)) from exc
    if total != before.st_size or _signature(before) != _signature(after):
        raise TemplateUnavailable("catalog member changed while hashing: " + str(path))
    member = {"path": path, "size": total, "sha256": sha.hexdigest(),
              "signature": _signature(before)}
    if _signature(_member_stat(path)) != member["signature"]:
        raise TemplateUnavailable("catalog member changed after hashing: " + str(path))
    return member


@dataclass(frozen=True)
class CatalogSnapshot:
    """One observed set of catalog bytes and their logical sources."""

    libraries: tuple
    entries: tuple
    snapshot_ref: str

    def describe(self):
        return {
            "schema": SNAPSHOT_SCHEMA,
            "snapshot_ref": self.snapshot_ref,
            "libraries": tuple(
                {"tier": row["tier"], "root": str(row["root"]),
                 "snapshot_ref": row["snapshot_ref"], "member_count": len(row["members"])}
                for row in self.libraries
            ),
        }


def build_snapshot(locations, catalog_paths):
    """Fingerprint enumerated package members, hashing each physical file once."""
    libraries, members, entries = [], {}, {}
    total = 0
    for location in locations:
        root = location.path.resolve()
        rows = []
        for path in catalog_paths.get(root, []):
            path = Path(path)
            if path not in members:
                member = _file_member(path)
                total += member["size"]
                if total > MAX_TOTAL_MEMBER_BYTES:
                    raise TemplateUnavailable("catalogs exceed snapshot byte budget")
                members[path] = member
            rows.append(members[path])
            sources = entries.setdefault(path, [])
            if location not in sources:
                sources.append(location)
        payload = {
            "root": str(root), "tier": location.tier, "legacy": location.legacy,
            "members": [{"path": str(row["path"]),
                         "sha256": row["sha256"], "size": row["size"]} for row in rows],
        }
        libraries.append({"root": root, "tier": location.tier, "members": rows,
                          "snapshot_ref": "snp_" + digest(payload)})
    ref = "snp_" + digest({"schema": SNAPSHOT_SCHEMA,
                           "libraries": [row["snapshot_ref"] for row in libraries]})
    return CatalogSnapshot(tuple(libraries), tuple(entries.items()), ref)
