"""Atomic text publication for file-based GUI handoff protocols."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


def atomic_publish_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    refuse_existing: bool = False,
) -> Path:
    """Publish text atomically, optionally refusing an existing destination."""
    destination = Path(path).expanduser()
    if not destination.parent.is_dir():
        raise FileNotFoundError(
            f"Transfer output directory does not exist: {destination.parent}"
        )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding=encoding, newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if refuse_existing:
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise FileExistsError(
                    f"Transfer output already exists: {destination}"
                ) from exc
        else:
            os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
