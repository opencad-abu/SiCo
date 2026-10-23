"""Session-scoped tool-profile setting: selection, storage and restore.

Interactive product sessions use the complete host-supported catalog as their
automatic envelope.  ``desktop_compat`` is retained as the historical id for
that surface; the broader flow set is not a user choice.  A profile is no
longer selected by ``<state>/ai/tool-profile.json``, which is a legacy file
that cannot narrow a new or restored product session.  The resolved value is
journaled as ``codex.tool.profile``; restoring an older product session
migrates its recorded narrow profile to the automatic surface.
Explicit embedding/CI callers can still call :meth:`ProfileSetting.select`
before the runtime is bound.  An unknown explicit choice remains an error.
"""

from __future__ import annotations

import json
from pathlib import Path

from sico.adapters.tool_profiles import PROFILES
from ..storage.journal import BULK_KINDS

AUTOMATIC_PROFILE = "desktop_compat"
PRODUCT_DEFAULT = AUTOMATIC_PROFILE
LEGACY_DEFAULT = "desktop_compat"
EVENT = "codex.tool.profile"
SCOPE_EVENT = "codex.tool.scope"
_MAX_BYTES = 4096


def validate(profile, profiles=PROFILES):
    if not isinstance(profile, str) or profile not in profiles:
        raise ValueError(
            "unknown tool profile "
            + repr(profile)
            + "; expected one of "
            + ", ".join(sorted(profiles))
        )
    return profile


def project_default_path(state_root) -> Path:
    return Path(state_root) / "ai" / "tool-profile.json"


def read_project_default(path) -> str | None:
    """Read the legacy bounded file; product sessions no longer consume it."""
    path = Path(path)
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if len(raw) > _MAX_BYTES:
        raise ValueError(f"{path}: tool-profile default exceeds {_MAX_BYTES} bytes")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"{path}: invalid tool-profile default") from exc
    if not isinstance(value, dict) or set(value) != {"default"}:
        raise ValueError(f"{path}: expected exactly one 'default' field")
    return validate(value["default"])


def resolve(*, session=None, project=None, user=None, profiles=PROFILES):
    """The first present source wins; returns ``(profile, source)``."""
    for value, source in ((session, "session"), (project, "project"), (user, "user")):
        if value is not None:
            return validate(value, profiles), source
    return PRODUCT_DEFAULT, "default"


class ProfileSetting:
    def __init__(self, backend, *, profiles=PROFILES):
        self.backend, self.profiles = backend, profiles
        records = list(backend.journal.events(exclude=BULK_KINDS))
        stored = [row["payload"] for row in records if row["kind"] == EVENT]
        if stored:
            profile = validate(stored[-1]["profile"], profiles)
            source = str(stored[-1].get("source", "session"))
            if profile == AUTOMATIC_PROFILE or source == "session":
                self.profile, self.source = profile, source
                return
            # Product defaults, project defaults and the old migration surface
            # are historical inputs, not a current capability decision.
            self.profile, self.source = AUTOMATIC_PROFILE, "automatic-migration"
            self._store(AUTOMATIC_PROFILE, "automatic-migration", previous=profile)
            return
        legacy = any(row["kind"] == "task.started" for row in records)
        self.profile = AUTOMATIC_PROFILE
        self.source = "automatic-migration" if legacy else "automatic"
        self._store(self.profile, self.source)

    def _store(self, profile, source, *, previous=None):
        payload = {"profile": profile, "source": source}
        if previous is not None:
            payload["previous"] = previous
        self.backend.task_journal.emit(EVENT, payload)

    def select(self, profile):
        """Explicit user choice; only before the runtime is bound to a scope."""
        profile = validate(profile, self.profiles)
        if profile == self.profile:
            return profile
        if getattr(self.backend, "runtime", None) is not None:
            raise ValueError(
                "tool profile changes require a new session; the current runtime "
                "is already bound to " + self.profile
            )
        self.profile, self.source = profile, "session"
        self._store(profile, "session")
        return profile


def record_scope(backend, scope) -> dict:
    """Journal this session's effective catalog and check it against the last one.

    The host records what this runtime actually serves (profile, tool count and
    the snapshot digest) and, when a session is resumed, whether the freshly
    resolved catalog still matches the recorded one; a mismatch is recorded as
    such instead of being silently replaced.
    """
    prior = None
    for row in backend.journal.events(exclude=BULK_KINDS):
        if row["kind"] == SCOPE_EVENT:
            prior = row["payload"]
    record = {
        "profile": scope["profile"],
        "count": len(scope["tools"]),
        "digest": scope["digest"],
        "prior_digest": prior.get("digest") if prior else None,
        "match": bool(prior) and prior.get("digest") == scope["digest"],
        "changed": bool(prior) and prior.get("digest") != scope["digest"],
    }
    backend.task_journal.emit(SCOPE_EVENT, record)
    return record


__all__ = [
    "AUTOMATIC_PROFILE", "EVENT", "LEGACY_DEFAULT", "PRODUCT_DEFAULT",
    "ProfileSetting", "SCOPE_EVENT", "project_default_path",
    "read_project_default", "record_scope", "resolve", "validate",
]
