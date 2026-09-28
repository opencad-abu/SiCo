"""Read-only validation of inventoried persistent state and internal references."""

import os
from pathlib import PurePosixPath

from ..core.contracts import TASK_CONTRACT, BoundContext, RunState, identifier
from ..storage.history_reader import validate_event
from ..storage.inbox_records import MAX_INPUT_RECORD, validate_input
from ..storage.journal import MAX_RECORD
from ..storage.project_files import RegistryError
from ..transport.framing import strict_json
from .filesystem import fingerprint, issue


def json_record(stream, limit=MAX_RECORD):
    raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("State record exceeds limit")
    value = strict_json(raw)
    if not isinstance(value, dict):
        raise ValueError("State record must be an object")
    return value


def references(value, session, inventory):
    """Verify attachment identities without rewriting immutable historical events."""
    if isinstance(value, dict):
        path = value.get("path")
        if isinstance(path, str) and path.startswith("attachments/"):
            relative = PurePosixPath(path)
            if relative.parent != PurePosixPath("attachments") / session:
                raise ValueError("Attachment crosses session boundary")
            entry = inventory.get("ai/agent/" + path)
            if (entry is None or entry["schema"] != "attachment"
                    or entry.get("sha256") != value.get("sha256")):
                raise ValueError("Missing or changed attachment")
            for field in ("size", "bytes"):
                if field in value and value[field] != entry["identity"]["size"]:
                    raise ValueError("Attachment size mismatch")
        for child in value.values():
            references(child, session, inventory)
    elif isinstance(value, list):
        for child in value:
            references(child, session, inventory)


def journal(stream, session, inventory):
    sequence = 0
    while True:
        raw = stream.readline(MAX_RECORD + 1)
        if not raw:
            return
        if len(raw) > MAX_RECORD or not raw.endswith(b"\n"):
            raise ValueError("Journal requires explicit recovery before migration")
        row = strict_json(raw)
        sequence += 1
        if (row.get("contract") != TASK_CONTRACT or row.get("session_id") != session
                or type(row.get("sequence")) is not int or row["sequence"] != sequence
                or not isinstance(row.get("kind"), str) or not isinstance(row.get("payload"), dict)):
            raise ValueError("Invalid journal envelope")
        RunState.from_record(row["state"])
        validate_event(row)
        references(row, session, inventory)
        yield row


def background(row, name):
    from ..service.background_retained import pending
    from ..service.execution_backend import backend_kind

    if (row.get("protocol") != "cad_ai_background_jobs.v1" or row.get("job_id") != name
            or row.get("automatic_resume_allowed") is not False):
        raise ValueError("Invalid background job identity")
    for key in ("job_id", "worker_id", "service_id", "owner_session_id"):
        identifier(row[key])
    BoundContext.from_record(row["origin"])
    if pending(row):
        return "unresolved_scheduler_job"
    if backend_kind(row) == "local" and not (
            row.get("state") == "completed" or row.get("state") in {"cancelled", "queue_timeout"}
            and row.get("operation_dispatched") is False):
        return "unresolved_local_job"
    return None


def validate_one(stream, entry, inventory):
    path, schema = entry["path"], entry["schema"]
    parts = PurePosixPath(path).parts
    if schema in {"attachment", "template_preview", "router_descriptor", "router_evidence"}:
        return None
    if schema == "workspace_settings":
        from sico_ui.workspace_settings import validate_stream

        validate_stream(stream)
        return None
    if schema in {"template_catalog", "template_capture"}:
        from .templates import validate_template

        validate_template(stream, entry, inventory)
        return None
    if schema == "journal":
        for _row in journal(stream, parts[3], inventory):
            pass
        if "/".join(parts[:-1]) + "/writer.lock" not in inventory:
            return "missing_writer_lock"
        return None
    limits = {"provider": 16384, "resources": 65536, "project": 16384,
              "inbox": MAX_INPUT_RECORD, "snapshot": 16384, "session_release": 16384}
    row = json_record(stream, limits.get(schema, MAX_RECORD))
    if schema == "snapshot":
        from ..storage.session_snapshot import validate

        validate(row, parts[3])
    elif schema == "session_release":
        # The session adapter validates this against the authoritative end event.
        return None
    elif schema == "provider":
        from ..providers.config import validate_config

        validate_config(row)
    elif schema == "resources":
        from ..codex.resource_config import validate_manifest

        validate_manifest(row)
    elif schema == "project":
        from ..service.project_identity import PROTOCOL, ProjectIdentity
        from ..service.service_discovery import local_host

        if row.pop("protocol", None) != PROTOCOL:
            raise ValueError("Invalid project identity protocol")
        identity = ProjectIdentity(**row)
        directory = inventory["ai/agent"]["identity"]
        lock = inventory["ai/agent/service/owner.lock"]["identity"]
        if (not identity.matches_location(directory["inode"], directory["device"],
                                          local_host().host_id)
                or identity.lock_inode != lock["inode"]):
            raise ValueError("Project identity differs from source directory")
        entry["object_id"] = identity.project_id
    elif schema == "inbox":
        validate_input(row, PurePosixPath(path).stem)
        references(row, parts[3], inventory)
    elif schema == "background":
        return background(row, PurePosixPath(path).stem)
    elif schema == "operation":
        from cadai.circuit_spec_schema import digest
        from cadai.project_contract import journal_record

        journal_record(row, row["key"])
        if digest(row["key"]) != PurePosixPath(path).stem:
            raise ValueError("Operation identity differs from filename")
        if row["state"] != "response_recorded":
            return "unresolved_operation"
    elif schema == "measurement":
        from cadai.result_contract import digest
        from cadai.result_tools import ARTIFACT_SCHEMAS

        prefix, expected = PurePosixPath(path).stem.rsplit("_", 1)
        if row.get("schema") != ARTIFACT_SCHEMAS[prefix] or digest(row) != expected:
            raise ValueError("Measurement schema or content hash mismatch")
    else:
        raise ValueError("No persistent schema validator")
    return None


def validate_records(tree, rows):
    from .native_history import validate_native_history
    from .routers import validate_routers
    from .templates import reference_conflicts

    inventory, blockers = {row["path"]: row for row in rows}, []
    for entry in rows:
        if entry["action"] not in {"retain", "identity"} or "sha256" not in entry:
            continue
        try:
            with tree.open(entry["path"]) as stream:
                if entry["path"].startswith("ai/agent/") and entry["identity"]["mode"] & 0o077:
                    raise ValueError("Private agent record has public permissions")
                if fingerprint(os.fstat(stream.fileno())) != entry["identity"]:
                    raise ValueError("State record changed during audit")
                code = validate_one(stream, entry, inventory)
                if fingerprint(os.fstat(stream.fileno())) != entry["identity"]:
                    raise ValueError("State record changed during audit")
                if code:
                    blockers.append(issue(entry["path"], code))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError,
                RegistryError):
            blockers.append(issue(entry["path"], "invalid_or_changed_record"))
    return (blockers + reference_conflicts(rows) + validate_routers(tree, rows)
            + validate_native_history(tree, rows))
