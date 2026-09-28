"""Stopped, persistent activation of an inventoried candidate with crash-safe publication."""

import fcntl
from sicolock import lock as state_lock
import os
import re
from pathlib import Path

from sicomigration import directory as validate_directory

from ..service.project_identity import create_identity, read_identity
from ..service.service_discovery import local_host
from ..storage.journal import open_private, sync_directory
from ..storage.project_files import read_record, write_record
from ..storage.project_lock import ProjectLock
from ..storage.project_locking import validate_locking
from .activation_layout import (
    FORMAT, MARKER, SERVICE, candidate_layout, file_hash, location, manifest,
    prepared_layout, published_identity,
)
from .copying import copy_entry, directory, save_document
from .directory_publish import publish
from .filesystem import Tree, fingerprint
from .history_seal import create as seal_history
from .prepare import PROTOCOL as PREPARE_FORMAT, source_inventory, stopped


def exists(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def candidate(transaction, tree, rows, digest):
    root = directory(transaction, "candidate")
    for entry in rows:
        if entry["action"] == "retain":
            copy_entry(tree, entry, root)
    sealed = seal_history(root, rows, digest)
    service = directory(root, SERVICE)
    with ProjectLock(service, create=True):
        host = local_host()
        try:
            identity = read_identity(service)
        except FileNotFoundError:
            identity = create_identity(service, host.host_id)
        identity.verify(service, host.host_id)
        marker = {"format": FORMAT, "inventory_sha256": digest,
                  "source_identity": tree.identity, "target_identity": location(root),
                  "project_identity": identity.record(),
                  "history_sha256": file_hash(root / next(iter(sealed))) if sealed else None,
                  "inventory_document_sha256": file_hash(transaction / "inventory.json")}
        save_document(root / MARKER, marker)
        candidate_layout(root, rows, digest, marker)
    sync_directory(root)
    sync_directory(transaction)
    return marker


def transaction_record(transaction, project, tree, digest):
    preparation = read_record(transaction / "transaction.json")
    expected = {"format": PREPARE_FORMAT, "inventory_sha256": digest, "project": str(project),
                "source_identity": tree.identity, "status": "prepared", "cutover": False}
    if preparation != expected:
        raise ValueError("Migration must have a matching prepared transaction")
    base = {"format": FORMAT, "project": str(project), "inventory_sha256": digest,
            "source_identity": tree.identity}
    path = transaction / "activation.json"
    try:
        record = read_record(path)
    except FileNotFoundError:
        if exists(project / ".sico") or exists(transaction / "candidate"):
            raise ValueError("Unowned target or candidate cannot be activated")
        record = dict(base, status="building", marker=None)
        write_record(path, record)
    if (set(record) != set(base) | {"status", "marker"}
            or any(record[key] != value for key, value in base.items())
            or record["status"] not in {"building", "ready", "activated"}
            or (record["status"] == "building") != (record["marker"] is None)):
        raise ValueError("Activation transaction identity conflict")
    return record


def activate_locked(project, tree, rows, digest):
    transaction = project / ".sico-migration" / digest
    validate_directory(transaction)
    manifest(transaction, project, tree.identity, rows, digest)
    prepared_layout(transaction, rows, digest)
    record = transaction_record(transaction, project, tree, digest)
    target, pending = project / ".sico", transaction / "candidate"
    if record["status"] == "activated":
        if exists(pending):
            raise ValueError("Activated transaction has an unexpected candidate")
        published_identity(target, record["marker"])
        return result(project, transaction, digest, record["marker"])
    if exists(target):
        if record["status"] != "ready" or exists(pending):
            raise ValueError("Migration target conflicts with the owned candidate")
        candidate_layout(target, rows, digest, record["marker"])
    else:
        if record["status"] == "building":
            marker = candidate(transaction, tree, rows, digest)
            record = dict(record, status="ready", marker=marker)
            write_record(transaction / "activation.json", record)
        else:
            candidate_layout(pending, rows, digest, record["marker"])
        # Hold the destination project lock across the publication boundary too.
        with ProjectLock(pending / SERVICE):
            _rows, current = source_inventory(tree)
            if current != digest or fingerprint((project / ".cad").lstat()) != tree.identity:
                raise ValueError("Legacy state changed before activation")
            stopped(project / ".cad")
            publish(pending, target)
    candidate_layout(target, rows, digest, record["marker"])
    write_record(transaction / "activation.json", dict(record, status="activated"))
    sync_directory(transaction)
    return result(project, transaction, digest, record["marker"])


def result(project, transaction, digest, marker):
    return {"format": FORMAT, "status": "activated", "cutover": True,
            "inventory_sha256": digest, "transaction": str(transaction),
            "target": str(project / ".sico"), "project_id": marker["project_identity"]["project_id"]}


def activate(project, *, expected_inventory, writers_stopped=False):
    """Internal API: the caller must attest remote/scheduler shutdown before cutover."""
    if writers_stopped is not True:
        raise ValueError("Activation requires explicit local and remote writer shutdown")
    if not isinstance(expected_inventory, str) or re.fullmatch("[a-f0-9]{64}", expected_inventory) is None:
        raise ValueError("Activation requires the reviewed inventory fingerprint")
    project = Path(project).absolute()
    control = project / ".sico-migration"
    validate_directory(control)
    validate_locking(control)
    lock = open_private(control / "owner.lock", os.O_RDWR)
    try:
        if os.fstat(lock).st_nlink != 1:
            raise ValueError("Invalid migration ownership lock")
        state_lock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        tree = Tree(project / ".cad", exclusive=True)
        try:
            rows, digest = source_inventory(tree)
            if digest != expected_inventory:
                raise ValueError("Legacy state differs from the reviewed inventory")
            stopped(project / ".cad")
            return activate_locked(project, tree, rows, digest)
        finally:
            tree.close()
    finally:
        os.close(lock)
