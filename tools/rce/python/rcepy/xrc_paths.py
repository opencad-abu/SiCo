"""Resolve Calibre XRC executables, corner paths and required files."""

from __future__ import annotations

import os
from pathlib import Path

from .config import RceConfig


def calibre_executable() -> str:
    mgc_home = os.environ.get("MGC_HOME", "").strip()
    return str(Path(mgc_home).expanduser() / "bin" / "calibre") if mgc_home else "calibre"


def xrc_corner_dir(cfg: RceConfig, corner: str) -> Path:
    tech_raw = cfg.text("extract", "tech_dir")
    tech_dir = cfg.resolve_path(tech_raw) if tech_raw else cfg.config_path.parent
    if not corner or tech_dir.name.casefold() == corner.casefold():
        return tech_dir
    exact = tech_dir / corner
    if exact.is_dir():
        return exact
    if tech_dir.is_dir():
        matches = sorted(
            (
                path
                for path in tech_dir.iterdir()
                if path.is_dir() and path.name.casefold() == corner.casefold()
            ),
            key=lambda path: path.name,
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(
                f"Ambiguous Calibre XRC corner {corner!r} under {tech_dir}: "
                + ", ".join(path.name for path in matches)
            )
    return exact


def require_xrc_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Cannot access {label}: {path}")
