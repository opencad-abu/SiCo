"""Recoverable backup helpers for generated CAD stage artifacts."""

from __future__ import annotations

from datetime import datetime
import shutil
from pathlib import Path
from collections.abc import Iterable


def backup_tag() -> str:
    """Return the standard timestamp tag used for stage backups."""
    return datetime.now().strftime("%m-%d-%H-%M-%S")


def available_backup_path(path: Path, tag: str) -> Path:
    """Choose a non-existing sibling path for a recoverable backup."""
    backup = path.with_name(f"{path.name}.{tag}")
    counter = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.{tag}.{counter}")
        counter += 1
    return backup


def backup_path(path: Path, tag: str) -> Path | None:
    """Move one existing path aside and return its backup path."""
    if not path.exists():
        return None
    backup = available_backup_path(path, tag)
    shutil.move(str(path), str(backup))
    return backup


def backup_existing(paths: Iterable[Path], tag: str | None = None) -> dict[Path, Path]:
    """Move existing stage paths aside and return original-to-backup mappings."""
    selected_tag = tag or backup_tag()
    result: dict[Path, Path] = {}
    for path in paths:
        backup = backup_path(path, selected_tag)
        if backup is not None:
            result[path] = backup
    return result
