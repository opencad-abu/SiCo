"""Resolve cdsTextTo5x and its installation-matched cdsLibDebug helper."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional


def find_executable(program: str) -> Optional[str]:
    """Resolve an explicit program path or a program available on PATH."""

    expanded = str(Path(program).expanduser()) if os.sep in program else program
    if os.sep in expanded:
        path = Path(expanded)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.absolute())
        return None
    return shutil.which(expanded)


def find_cds_lib_debug(
    cds_text_to_5x: str,
    requested: Optional[str],
    *,
    allow_path_lookup: bool,
) -> Optional[str]:
    """Find the cdsLibDebug companion from the selected Cadence install."""

    if requested:
        return find_executable(requested)
    executable = Path(cds_text_to_5x).absolute()
    install_roots: List[Path] = []
    for ancestor in executable.parents:
        if ancestor.name in {"tools", "tools.lnx86"}:
            install_roots.append(ancestor.parent)
        if ancestor.name in {"bin", "share"}:
            install_roots.append(ancestor.parent)
    for install_root in dict.fromkeys(install_roots):
        for candidate in (
            install_root / "tools/bin/cdsLibDebug",
            install_root / "tools.lnx86/bin/cdsLibDebug",
            install_root / "bin/cdsLibDebug",
        ):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    if allow_path_lookup:
        return shutil.which("cdsLibDebug")
    return None
