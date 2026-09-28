"""Prepare backed-up state after explicit writer shutdown; never switch live roots."""

import fcntl
from sicolock import lock as state_lock
import hashlib
import json
import os
import re
from pathlib import Path

from ..storage.journal import open_private, sync_directory
from ..storage.project_files import read_record, write_record
from ..storage.project_locking import validate_locking
from .audit import audit
from .copying import copy_entry, directory, parent_paths, save_document, verify, verify_layout
from .filesystem import Tree, fingerprint
from .history_seal import create as seal_history
from .activation_layout import inventory_document
from .processes import scan_local
from .records import validate_records
from .sessions import validate_sessions

PROTOCOL = "sico.state.migration.prepare.v1"


def source_inventory(tree):
    rows, blockers = tree.inventory()
    blockers.extend(validate_records(tree, rows))
    blockers.extend(validate_sessions(tree, rows))
    if blockers:
        raise ValueError("Source changed or has unresolved migration blockers")
    inventory = {"source_identity": tree.identity, "entries": rows}
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()
    return rows, digest


def stopped(source):
    result = scan_local(source)
    if result["referencing_pids"] or result["unreadable_processes"]:
        raise ValueError("Local writer shutdown cannot be established")


def prepare(project, *, expected_inventory, writers_stopped=False):
    """A caller must drain remote writers as well; a local scan cannot attest for NAS."""
    if writers_stopped is not True:
        raise ValueError("Preparation requires explicit local and remote writer shutdown")
    if not isinstance(expected_inventory, str) or not re.fullmatch("[a-f0-9]{64}", expected_inventory):
        raise ValueError("Preparation requires the reviewed inventory fingerprint")
    report = audit(project)
    if report["blockers"] or report["inventory_sha256"] != expected_inventory:
        raise ValueError("Migration audit is blocked or differs from the reviewed inventory")
    project = Path(report["project"])
    validate_locking(project)
    control = directory(project / ".sico-migration")
    sync_directory(project)
    lock = open_private(control / "owner.lock", os.O_CREAT | os.O_RDWR)
    try:
        if os.fstat(lock).st_nlink != 1:
            raise ValueError("Invalid migration ownership lock")
        state_lock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        tree = Tree(project / ".cad", exclusive=True)
        try:
            rows, current = source_inventory(tree)
            if current != expected_inventory:
                raise ValueError("Source changed before migration ownership")
            stopped(project / ".cad")
            return prepare_transaction(project, control, tree, rows, expected_inventory)
        finally:
            tree.close()
    finally:
        os.close(lock)


def prepare_transaction(project, control, tree, rows, digest):
    transaction = directory(control, digest)
    backup = directory(transaction, "backup")
    staged = directory(transaction, "staged")
    record = {"format": PROTOCOL, "inventory_sha256": digest, "project": str(project),
              "source_identity": tree.identity, "status": "copying", "cutover": False}
    receipt = transaction / "transaction.json"
    try:
        previous = read_record(receipt)
    except FileNotFoundError:
        write_record(receipt, record)
    else:
        if (previous.get("status") not in {"copying", "prepared"}
                or {key: value for key, value in previous.items() if key != "status"}
                != {key: value for key, value in record.items() if key != "status"}):
            raise ValueError("Migration transaction identity conflict")
    save_document(transaction / "inventory.json",
                  inventory_document(project, backup, project / ".sico",
                                     tree.identity, rows, digest))
    retained = [row for row in rows if row["action"] == "retain"]
    original = retained + [row for row in rows if row["action"] in {"identity", "archive"}]
    for entry in original:
        copy_entry(tree, entry, backup)
    for entry in retained:
        copy_entry(tree, entry, staged)
    sealed = seal_history(staged, rows, digest)
    # Identity/lock binding is created only at activation; old credentials and runtime
    # discovery never enter the new state. Originals remain in the exact-byte backup.
    for root, entries in ((backup, original), (staged, retained)):
        files = {entry["path"] for entry in entries}
        if root == staged:
            files |= sealed
        verify_layout(root, files, parent_paths(files))
        for entry in entries:
            verify(root / entry["path"], entry)
    _rows, checked = source_inventory(tree)
    if checked != digest or fingerprint((project / ".cad").lstat()) != tree.identity:
        raise ValueError("Migration source changed while copying")
    stopped(project / ".cad")
    try:
        (project / ".sico").lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("Migration target appeared during preparation")
    write_record(receipt, dict(record, status="prepared"))
    sync_directory(transaction)
    return {"format": PROTOCOL, "status": "prepared", "cutover": False,
            "inventory_sha256": digest, "transaction": str(transaction),
            "retained_files": len(retained), "backup_files": len(original),
            "target": str(project / ".sico")}
