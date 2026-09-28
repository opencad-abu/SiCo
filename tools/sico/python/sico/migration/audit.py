"""Build a read-only migration inventory; audit never authorizes a live cutover."""

import hashlib
import json
import os
import stat
from collections import Counter
from pathlib import Path

from ..storage.project_files import RegistryError
from .filesystem import Tree, fingerprint, issue
from .processes import scan_local
from .records import validate_records
from .sessions import validate_sessions

PROTOCOL = "sico.state.migration.audit.v1"


def audit(project):
    launch = Path(project).absolute()
    if not launch.is_dir():
        raise ValueError("Project directory is unavailable")
    source, target = launch / ".cad", launch / ".sico"
    rows, blockers, identity = [], [], None
    try:
        info = source.lstat()
    except FileNotFoundError:
        blockers.append(issue(".cad", "legacy_root_missing"))
    else:
        if not stat.S_ISDIR(info.st_mode):
            blockers.append(issue(".cad", "unsafe_legacy_root"))
        else:
            try:
                tree = Tree(source)
                try:
                    identity = tree.identity
                    rows, errors = tree.inventory()
                    blockers.extend(errors)
                    blockers.extend(validate_records(tree, rows))
                    blockers.extend(validate_sessions(tree, rows))
                    if fingerprint(source.lstat()) != identity:
                        blockers.append(issue(".cad", "root_changed"))
                finally:
                    tree.close()
            except RegistryError:
                blockers.append(issue(".cad", "lock_policy_unverified"))
            except (OSError, ValueError):
                blockers.append(issue(".cad", "legacy_root_unreadable"))
    try:
        target.lstat()
    except FileNotFoundError:
        pass
    else:
        # No auto-merge, including same project_id on distinct directory inodes.
        blockers.append(issue(".sico", "target_exists"))
    local = scan_local(source)
    if local["referencing_pids"]:
        blockers.append(issue(".cad", "local_process_references"))
    if local["unreadable_processes"]:
        blockers.append(issue(".cad", "local_process_scan_incomplete"))
    blockers = sorted({(row["path"], row["code"]) for row in blockers})
    inventory = {"source_identity": identity, "entries": rows}
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()
    return {"format": PROTOCOL, "mode": "read_only", "project": str(launch),
            "source": str(source), "target": str(target), **inventory,
            "inventory_sha256": digest, "counts": dict(sorted(Counter(
                row["action"] for row in rows).items())), "process_observation": local,
            "blockers": [issue(path, code) for path, code in blockers],
            "eligible_for_stopped_migration": not blockers,
            "cutover_authorized": False,
            "required_before_apply": ["stop_local_and_remote_writers",
                                      "verify_scheduler_jobs_drained",
                                      "hold_source_locks_and_revalidate_inventory"]}
