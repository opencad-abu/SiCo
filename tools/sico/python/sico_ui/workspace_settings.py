"""Qt workspace layout and appearance persistence with snapshot validation."""

import os
import tempfile
from pathlib import Path

from PyQt5.QtCore import QByteArray, QSettings

from .appearance import KEYS as APPEARANCE_KEYS
from .appearance import validate_stored_appearance

MAX_BYTES = 1024 * 1024
LAYOUT_KEYS = frozenset({"workspace/geometry", "workspace/state"})
KEYS = LAYOUT_KEYS | APPEARANCE_KEYS


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
            if any(not isinstance(settings.value(key), QByteArray)
                   for key in keys & LAYOUT_KEYS):
                raise ValueError("Workspace layout must contain Qt byte arrays")
            validate_stored_appearance(settings)
        finally:
            del settings
