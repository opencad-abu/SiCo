"""Explicit runtime switch from Codex's native shell to the session terminal."""

NATIVE_SHELL_OPTIONS = {
    "features.shell_tool": False,
    "features.shell_snapshot": False,
    "features.unified_exec": False,
    "features.unified_exec_tty": False,
}


def options(enabled):
    """Return the feature overrides for a selected session-terminal route."""
    if type(enabled) is not bool:
        raise ValueError("Terminal route selection must be boolean")
    return dict(NATIVE_SHELL_OPTIONS) if enabled else {}
