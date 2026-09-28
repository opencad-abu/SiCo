"""Validate activation payloads against the reviewed source and generated identities."""

import hashlib
import json
import os
from pathlib import Path

from sicomigration import directory as validate_directory
from sicomigration import FORMAT, MARKER, SERVICE

from ..service.project_identity import read_identity
from ..storage.history_policy import NAME as HISTORY_NAME
from ..storage.journal import open_private
from ..storage.project_files import read_record
from .copying import parent_paths, verify, verify_layout

INVENTORY_FORMAT = "sico.state.migration.inventory.v1"

def location(path):
    info = path.lstat()
    return {"device": info.st_dev, "inode": info.st_ino, "uid": info.st_uid}


def file_hash(path):
    digest = hashlib.sha256()
    with os.fdopen(open_private(path, os.O_RDONLY), "rb") as stream:
        if os.fstat(stream.fileno()).st_nlink != 1:
            raise ValueError("Migration metadata has unexpected links")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def document(path, expected):
    raw = (json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with os.fdopen(open_private(path, os.O_RDONLY), "rb") as stream:
        if os.fstat(stream.fileno()).st_nlink != 1 or stream.read(len(raw) + 1) != raw:
            raise ValueError("Migration document differs from its reviewed identity")


def prepared_layout(transaction, rows, digest):
    from .history_seal import policy

    retained = [row for row in rows if row["action"] == "retain"]
    originals = retained + [row for row in rows if row["action"] in {"archive", "identity"}]
    value = policy(rows, digest)
    for name, entries in (("backup", originals), ("staged", retained)):
        root = transaction / name
        validate_directory(root)
        files = {row["path"] for row in entries}
        if name == "staged" and value is not None:
            files.add("ai/agent/" + HISTORY_NAME)
            document(root / "ai/agent" / HISTORY_NAME, value)
        verify_layout(root, files, parent_paths(files))
        for entry in entries:
            verify(root / entry["path"], entry)


def candidate_layout(root, rows, digest, marker):
    from .history_seal import policy

    validate_directory(root)
    retained = [row for row in rows if row["action"] == "retain"]
    files = {row["path"] for row in retained} | {SERVICE + "/owner.lock", SERVICE + "/project.json", MARKER}
    value = policy(rows, digest)
    if value is not None:
        files.add("ai/agent/" + HISTORY_NAME)
        document(root / "ai/agent" / HISTORY_NAME, value)
    verify_layout(root, files, parent_paths(files))
    for entry in retained:
        verify(root / entry["path"], entry)
    document(root / MARKER, marker)
    published_identity(root, marker)


def published_identity(root, marker):
    expected_marker = {"format", "inventory_sha256", "source_identity", "target_identity",
                       "project_identity", "history_sha256", "inventory_document_sha256"}
    if set(marker) != expected_marker or marker["format"] != FORMAT:
        raise ValueError("Published migration marker has an invalid shape")
    if location(root) != marker["target_identity"]:
        raise ValueError("Published migration belongs to another directory")
    identity = read_identity(root / SERVICE)
    if identity.record() != marker["project_identity"]:
        raise ValueError("Published migration has another project identity")
    identity.verify(root / SERVICE, identity.created_host)
    expected = marker["history_sha256"]
    history = root / "ai/agent" / HISTORY_NAME
    if expected is not None:
        if file_hash(history) != expected:
            raise ValueError("Published history policy changed")
    elif history.exists():
        raise ValueError("Unexpected historical session policy")
    if read_record(root / MARKER) != marker:
        raise ValueError("Published activation marker changed")


def inventory_document(project, backup, target, identity, rows, digest):
    return {"format": INVENTORY_FORMAT,
        "source": str(project / ".cad"), "target": str(project / ".sico"),
        "source_identity": identity, "inventory_sha256": digest, "entries": rows,
        "locations": [{"source": str(project / ".cad" / row["path"]),
                       "target": str((backup if row["action"] == "archive" else target)
                                     / row["path"]),
                       "sha256": row["sha256"]}
                      for row in rows if row["action"] in {"retain", "archive"}]}


def manifest(transaction, project, identity, rows, digest):
    expected = inventory_document(project, transaction / "backup", project / ".sico",
                                  identity, rows, digest)
    document(transaction / "inventory.json", expected)
