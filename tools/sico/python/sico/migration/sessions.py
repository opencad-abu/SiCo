"""Admission of session history using the same facts as explicit recovery."""

import os
from contextlib import contextmanager
from pathlib import PurePosixPath

from ..service.recovery_facts import collect_facts
from ..service.service_session_dto import SessionAddress
from ..storage.inbox_records import MAX_INPUT_RECORD, validate_input
from ..storage.project_files import MAX_RECORD, RegistryError
from ..storage.session_snapshot import validate as validate_snapshot
from .filesystem import fingerprint, issue
from .records import journal, json_record


@contextmanager
def checked_stream(tree, entry):
    with tree.open(entry["path"]) as stream:
        if ("sha256" not in entry or entry["identity"]["mode"] & 0o077
                or fingerprint(os.fstat(stream.fileno())) != entry["identity"]):
            raise ValueError("Session record changed before validation")
        yield stream
        if fingerprint(os.fstat(stream.fileno())) != entry["identity"]:
            raise ValueError("Session record changed during validation")


def read_json(tree, entry, limit=MAX_RECORD):
    with checked_stream(tree, entry) as stream:
        return json_record(stream, limit)


def validate_address(row, session_id, project_id):
    address = SessionAddress.from_record(row)
    if (address.session.session_id != session_id or address.project_id != project_id):
        raise ValueError("Historical address belongs to another project or session")


def inputs(tree, entries, session_id, project_id):
    for entry in entries:
        row = read_json(tree, entry, MAX_INPUT_RECORD)
        validate_input(row, PurePosixPath(entry["path"]).stem)
        if row.get("service_address") is not None:
            validate_address(row["service_address"], session_id, project_id)
        yield row


def events(stream, session_id, project_id, inventory):
    for row in journal(stream, session_id, inventory):
        if row["kind"] in {"session.end_requested", "session.end_observed",
                            "session.input_abandoned"}:
            address = row["payload"].get("address")
            if address is not None:
                validate_address(address, session_id, project_id)
        yield row


def validate_session(tree, session_id, entries, inventory):
    by_schema = {entry["schema"]: entry for entry in entries if entry["schema"] != "inbox"}
    if "journal" not in by_schema:
        return "missing_session_journal"
    snapshot = by_schema.get("snapshot")
    saved = validate_snapshot(read_json(tree, snapshot), session_id) if snapshot else None
    project_id = inventory.get("ai/agent/service/project.json", {}).get("object_id")
    if saved and saved["project_id"] is not None and saved["project_id"] != project_id:
        raise ValueError("Session snapshot belongs to another project")
    release_entry = by_schema.get("session_release")
    release = read_json(tree, release_entry) if release_entry else None
    input_rows = inputs(tree, [entry for entry in entries if entry["schema"] == "inbox"],
                        session_id, project_id)
    with checked_stream(tree, by_schema["journal"]) as stream:
        facts = collect_facts(session_id, events(stream, session_id, project_id, inventory),
                              saved, input_rows, lambda event: release)
    if release_entry and (not facts.ends or facts.ends[-1]["kind"] != "session.end_observed"):
        raise ValueError("Release receipt has no corresponding end event")
    # Deletion hides a conversation from the UI; it cannot drain external work.
    if (facts.inputs or facts.task.get("status") in {"executing", "needs_reconcile"}
            or facts.ends and not facts.resources_released):
        return "unresolved_session"
    return None


def validate_sessions(tree, rows):
    inventory, sessions, blockers = {entry["path"]: entry for entry in rows}, {}, []
    for entry in rows:
        if entry["schema"] in {"journal", "snapshot", "session_release", "inbox"}:
            session_id = PurePosixPath(entry["path"]).parts[3]
            sessions.setdefault(session_id, []).append(entry)
    for session_id, entries in sessions.items():
        path = "ai/agent/sessions/" + session_id
        try:
            code = validate_session(tree, session_id, entries, inventory)
            if code:
                blockers.append(issue(path, code))
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError,
                RegistryError):
            blockers.append(issue(path, "invalid_or_changed_session"))
    return blockers
