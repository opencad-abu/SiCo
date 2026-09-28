"""Derive one immutable-session registry from the validated migration inventory."""

from pathlib import PurePosixPath

from ..storage.history_policy import FORMAT, NAME, validate
from .copying import save_document


def policy(entries, inventory_sha256):
    sessions, project_id = {}, None
    for entry in entries:
        if entry["schema"] == "project":
            project_id = entry["object_id"]
        if entry["schema"] in {"journal", "snapshot"}:
            session_id = PurePosixPath(entry["path"]).parent.name
            row = sessions.setdefault(session_id, {"journal_sha256": None, "snapshot_sha256": None})
            row[entry["schema"] + "_sha256"] = entry["sha256"]
    if not sessions:
        return None
    return validate(dict(format=FORMAT, inventory_sha256=inventory_sha256,
                         source_project_id=project_id, sessions=sessions))


def create(staged, entries, inventory_sha256):
    value = policy(entries, inventory_sha256)
    if value is None:
        return set()
    relative = "ai/agent/" + NAME
    save_document(staged / relative, value)
    return {relative}
