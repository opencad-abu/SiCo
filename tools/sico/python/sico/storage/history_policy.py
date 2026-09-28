"""Authoritative read-only membership for histories sealed during migration."""

import hashlib
import os
import re
from pathlib import Path

from sicomigration import admission

from ..core.contracts import identifier
from ..transport.framing import strict_json

NAME = "history-migration.json"
FORMAT = "sico.history.migration.v1"
MAX_BYTES = 32 * 1024 * 1024


def digest(value):
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value) is not None


def validate(value):
    fields = {"format", "inventory_sha256", "source_project_id", "sessions"}
    if (not isinstance(value, dict) or set(value) != fields or value["format"] != FORMAT
            or not digest(value["inventory_sha256"])):
        raise ValueError("Invalid historical session policy")
    project = value["source_project_id"]
    if project is not None and (not isinstance(project, str) or re.fullmatch("[a-f0-9]{32}", project) is None):
        raise ValueError("Invalid historical project identity")
    sessions = value["sessions"]
    if not isinstance(sessions, dict) or len(sessions) > 100000:
        raise ValueError("Invalid historical session registry")
    for session_id, row in sessions.items():
        identifier(session_id)
        if (not isinstance(row, dict) or set(row) != {"journal_sha256", "snapshot_sha256"}
                or not digest(row["journal_sha256"])
                or row["snapshot_sha256"] is not None and not digest(row["snapshot_sha256"])):
            raise ValueError("Invalid historical session evidence")
    return value


def read(root):
    from .journal import open_private

    root = Path(root)
    state_root = root.parent.parent
    if state_root.name == ".sico":
        admission(state_root)
    try:
        fd = open_private(Path(root) / NAME, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        if os.fstat(stream.fileno()).st_nlink != 1:
            raise ValueError("Historical session policy has unexpected links")
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Historical session policy exceeds limit")
    return validate(strict_json(raw))


def assert_writable(root, session_id):
    identifier(session_id)
    policy = read(root)
    if policy is not None and session_id in policy["sessions"]:
        raise ValueError("迁移历史保持只读；请新建会话继续工作")


def verify(root, session_id):
    """Return provenance only after checking immutable journal and snapshot bytes."""
    from .journal import open_private

    identifier(session_id)
    policy = read(root)
    if policy is None or session_id not in policy["sessions"]:
        return None
    directory = Path(root) / "sessions" / session_id
    entry = policy["sessions"][session_id]
    for name, field in (("events.jsonl", "journal_sha256"),
                        ("session.snapshot.json", "snapshot_sha256")):
        path, expected = directory / name, entry[field]
        if expected is None:
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            raise ValueError("Historical snapshot appeared after migration")
        with os.fdopen(open_private(path, os.O_RDONLY), "rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_nlink != 1:
                raise ValueError("Historical evidence has unexpected links")
            actual = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                actual.update(chunk)
            after = os.fstat(stream.fileno())
        if (actual.hexdigest() != expected or
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError("Historical evidence changed after migration")
    return policy
