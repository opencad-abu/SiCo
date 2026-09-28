"""Recognize missing session components without launching or repairing a runtime."""

import os
import stat

from ..core.contracts import identifier
from ..transport.framing import strict_json
from .history_reader import owned_directory
from .journal import open_private
from .record_removal import MESSAGE, assert_present

MANAGED_EVENTS = frozenset({"session.control", "session.recovered", "session.end_requested",
                            "session.end_observed", "binding.selected"})


class IncompleteSession(ValueError):
    pass


def require_components(root, session_id, *, managed=False, empty=False, thread=None):
    assert_present(root, session_id)
    directory = root / "sessions" / identifier(session_id)
    try:
        for path in (root, root / "sessions", directory):
            owned_directory(path)
        fd = open_private(directory / "events.jsonl", os.O_RDONLY)
        os.close(fd)
        if managed or empty:
            fd = open_private(directory / "session.snapshot.json", os.O_RDONLY)
            os.close(fd)
        if empty and any((directory / "end-releases").glob("*.json")):
            raise IncompleteSession(MESSAGE)
        if thread and thread.get("history_mode") == "paginated":
            _native_history(directory / "codex", thread["thread_id"])
    except FileNotFoundError:
        raise IncompleteSession(MESSAGE) from None


def _native_history(home, thread_id):
    identifier(thread_id)
    owned_directory(home)
    # Native paginated threads retain a rollout even when their database is rebuilt.
    # Match only this recorded identity, including Codex's archived history location.
    candidates = []
    for name, pattern in (("sessions", "*/*/*/rollout-*"),
                          ("archived_sessions", "rollout-*")):
        candidates.extend((home / name).glob(pattern + thread_id + ".jsonl"))
    for path in candidates:
        for parent in path.relative_to(home).parents:
            info = (home / parent).lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError("Invalid native history directory")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise ValueError("Invalid native history file")
            try:
                row = strict_json(stream.readline(1048577))
                if row.get("type") == "session_meta" and row.get("payload", {}).get("id") == thread_id:
                    return
            except (ValueError, AttributeError):
                pass
    raise IncompleteSession(MESSAGE)
