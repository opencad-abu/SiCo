"""Select the native Codex home shared by one project's sessions."""

from pathlib import Path

PROJECT_HOME = "codex"
SESSION_HOME = "codex"


def project_native_home(root):
    return Path(root) / PROJECT_HOME


def session_native_home(root, session_id):
    return Path(root) / "sessions" / session_id / SESSION_HOME


def selected_native_home(root, session_id):
    """Keep an existing session home; new sessions share the project home."""
    legacy = session_native_home(root, session_id)
    try:
        legacy.lstat()
    except FileNotFoundError:
        return project_native_home(root), "project"
    return legacy, "legacy-session"
