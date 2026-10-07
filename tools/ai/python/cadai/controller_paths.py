"""Resolve source and native installation roots for one controller session."""

from __future__ import annotations

import os
from pathlib import Path

from .installation import current, runtime_root


def session_roots() -> tuple[Path, Path]:
    tool = current().tool("ai")
    return tool / "python", tool


def session_install_roots(
    source_root: Path, terminal_executable: str
) -> tuple[Path, ...]:
    """Find native runtime roots without changing controller/skills ownership."""
    return (runtime_root(os.environ, (source_root,)),)
