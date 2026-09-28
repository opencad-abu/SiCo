"""Quantus foundry and user blocking-cell list handling."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .config import DesignContext, RceConfig
from .pathutil import resolve_path
from .textutil import q, write_text


QRC_BLOCK_CELL_ENV = "RCE_QUANTUS_BLOCK_CELL_FILE"


def qrc_cell_options(cfg: RceConfig, ctx: DesignContext, tech_dir: Path) -> list[str]:
    """Return the dynamic blocking-cell file option, if a list is selected."""
    user_cells = cfg.blocked_cells()
    foundry_file = _foundry_cell_file(tech_dir)

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


def _foundry_cell_file(tech_dir: Path) -> Path | None:
    configured = os.environ.get(QRC_BLOCK_CELL_ENV, "").strip()
    if not configured:
        return None
    try:
        path = resolve_path(configured, tech_dir)
    except ValueError as exc:
        raise ValueError(f"Invalid {QRC_BLOCK_CELL_ENV}: {exc}") from exc
    if not path.is_file():
        raise FileNotFoundError(
            f"Cannot access Quantus block-cell file configured by "
            f"{QRC_BLOCK_CELL_ENV}: {path}"
        )
    return path
