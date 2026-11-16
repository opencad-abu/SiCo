"""Quantus foundry and user blocking-cell list handling."""

from __future__ import annotations

import re
from pathlib import Path

from .config import DesignContext, RceConfig
from .pathutil import resolve_path
from .textutil import q, write_text
from .qrc_technology import extract_tech_dirs


QRC_BLOCK_CELL_ENV = "RCE_QUANTUS_BLOCK_CELL_FILE"


def qrc_cell_options(cfg: RceConfig, ctx: DesignContext, tech_dir: Path) -> list[str]:
    """Return the dynamic blocking-cell file option, if a list is selected."""
    user_cells = cfg.blocked_cells()
    foundry_file = qrc_foundry_cell_file(cfg, (tech_dir,) if not cfg.flag("extract", "finfet_flow")
                                         else extract_tech_dirs(cfg))

    cells_file: Path | None = foundry_file
    if user_cells and foundry_file is not None:
        cells_file = ctx.log_dir / "cells.merged"
        write_text(cells_file, _merged_cell_list(foundry_file, user_cells))
    elif user_cells:
        cells_file = ctx.log_dir / "cells"
        write_text(cells_file, "".join(f"{cell}\n" for cell in user_cells))

    if cells_file is None:
        return []

    return [
        f'-parasitic_blocking_device_cells_file "{q(str(cells_file))}"'
    ]


def qrc_foundry_cell_file(cfg: RceConfig, tech_dirs: tuple[Path, ...]) -> Path | None:
    configured = cfg.text("extract", "qrc", "block_cell_file").strip()
    files = [_foundry_cell_file(cfg, directory) for directory in tech_dirs]
    if not configured and cfg.flag("extract", "finfet_flow"):
        fallback = []
        for directory in tech_dirs:
            path = next(
                (candidate / "hcell_qci"
                 for candidate in (directory, directory.parent, directory.parent.parent)
                 if (candidate / "hcell_qci").is_file()),
                None,
            )
            if path is None:
                return None
            fallback.append(path)
        if len({path.read_bytes() for path in fallback}) > 1:
            raise ValueError("Quantus FinFET corners have incompatible hcell_qci files")
        return fallback[0]
    if any(file is None for file in files):
        return None
    if len({file.read_bytes() for file in files}) > 1:
        raise ValueError("Quantus FinFET corners have incompatible blocking-cell files")
    return files[0]


def _merged_cell_list(foundry_file: Path, user_cells: list[str]) -> str:
    try:
        foundry_text = foundry_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(
            f"Cannot read QRC foundry blocking-cell file: {foundry_file}: {exc}"
        ) from exc

    foundry_patterns = {
        line.split()[0]
        for line in foundry_text.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "+"))
    }
    additions = [
        cell
        for cell in user_cells
        if not any(_matches_quantus_pattern(pattern, cell) for pattern in foundry_patterns)
    ]
    if not additions:
        return foundry_text

    separator = "" if not foundry_text or foundry_text.endswith("\n") else "\n"
    return foundry_text + separator + "".join(f"{cell}\n" for cell in additions)


def _matches_quantus_pattern(pattern: str, cell: str) -> bool:
    expression = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.fullmatch(expression, cell) is not None


def _foundry_cell_file(cfg: RceConfig, tech_dir: Path) -> Path | None:
    configured = cfg.text("extract", "qrc", "block_cell_file").strip()
    if not configured:
        return None
    try:
        path = resolve_path(configured, tech_dir)
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path
