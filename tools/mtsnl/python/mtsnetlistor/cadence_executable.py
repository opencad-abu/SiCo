"""Resolve a runnable Cadence executable in the child environment."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Optional, Mapping
from .errors import RequestValidationError


def _cadence_executable(
    value: Optional[str],
    default: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    candidate = value or default
    path = Path(candidate).expanduser()
    if path.parent != Path("."):
        path = path.resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RequestValidationError(f"Cadence executable is not runnable: {path}")
        return str(path)
    resolved = shutil.which(
        candidate,
        path=None if environ is None else environ.get("PATH"),
    )
    if not resolved:
        raise RequestValidationError(f"Cadence executable is not on PATH: {candidate}")
    return resolved
