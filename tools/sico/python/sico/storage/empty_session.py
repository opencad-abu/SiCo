"""Discard complete session records that never received a user input."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..core.contracts import identifier
from ..core.session_events import SYSTEM_ORIGINS
from .history_reader import SessionReader, owned_directory
from .inbox_records import read_input
from .journal import sync_directory
from .record_removal import writer_lock

LOGGER = logging.getLogger(__name__)


def _has_user_input(reader):
    inbox = reader.directory / "inbox"
    if inbox.exists():
        owned_directory(inbox)
        for path in sorted(inbox.glob("*.json")):
            row = read_input(inbox, path.stem)
            if row is None:
                raise ValueError("Session input changed while reading")
            if row["origin"] not in SYSTEM_ORIGINS:
                return True
    for event in reader.stream_events():
        if event["kind"] == "task.started":
            # Legacy task rows omit origin; preserve them instead of guessing.
            if event["payload"].get("origin", "chat") not in SYSTEM_ORIGINS:
                return True
    return False


def discard_empty_session(root, session_id):
    """Delete one closed session only when no user input exists.

    The writer lock excludes an active service. Unreadable or incomplete
    evidence is preserved for the existing damaged-record workflow.
    """

    root = Path(root)
    session_id = identifier(session_id)
    directory = root / "sessions" / session_id
    if not directory.exists():
        return False
    with writer_lock(root, session_id):
        try:
            reader = SessionReader(root, session_id)
            # Imported history has its own review and retention policy.
            if reader.migration is not None or _has_user_input(reader):
                return False
        except FileNotFoundError:
            return False
        attachments = root / "attachments" / session_id
        targets = [directory]
        if attachments.exists() or attachments.is_symlink():
            owned_directory(attachments)
            targets.append(attachments)
        for target in targets:
            shutil.rmtree(target)
        for parent in (root / "sessions", root / "attachments"):
            if parent.exists():
                sync_directory(parent)
    return True


def discard_empty_sessions(root):
    """Discard every closed no-input session below one agent root.

    A session that cannot be read or validated is left in place; service
    shutdown must not turn cleanup uncertainty into data loss.
    """

    root = Path(root)
    sessions = root / "sessions"
    if not sessions.exists():
        return ()
    try:
        owned_directory(sessions)
        paths = sorted(sessions.iterdir())
    except (OSError, ValueError) as exc:
        LOGGER.warning("Skipped empty-session cleanup: %s", exc)
        return ()
    discarded = []
    for path in paths:
        try:
            session_id = identifier(path.name)
            if not path.is_dir() or path.is_symlink():
                continue
            if discard_empty_session(root, session_id):
                discarded.append(session_id)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            LOGGER.warning("Preserved session %s during empty-session cleanup: %s",
                           path.name, exc)
    return tuple(discarded)
