"""Qt workspace layout persistence and offline snapshot validation."""

import os
import tempfile
from pathlib import Path

from PyQt5.QtCore import QByteArray, QSettings

MAX_BYTES = 1024 * 1024
KEYS = frozenset({"workspace/geometry", "workspace/state"})


def open_settings(path):
    settings = QSettings(str(path), QSettings.IniFormat)
    settings.setFallbacksEnabled(False)
    return settings


def validate_stream(stream):
    """Let Qt decode a private snapshot; never expose the source to a Qt writer."""
    raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Workspace layout exceeds limit")
    with tempfile.TemporaryDirectory(prefix="sico-workspace-audit-") as temporary:
        path = Path(temporary) / "workspace.ini"
        with path.open("xb") as output:
            os.chmod(path, 0o600)
            output.write(raw)
        settings = open_settings(path)
        try:
            keys = set(settings.allKeys())
            if settings.status() != QSettings.NoError or not keys <= KEYS:
                raise ValueError("Unsupported workspace settings")
            if any(not isinstance(settings.value(key), QByteArray) for key in keys):
                raise ValueError("Workspace layout must contain Qt byte arrays")
        finally:
            del settings
