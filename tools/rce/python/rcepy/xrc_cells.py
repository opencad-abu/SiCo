"""Validate and write Calibre XRC cell selection lists."""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

from .xrc_models import XrcSettings


def write_xrc_cell_lists(settings: XrcSettings) -> None:
    """Write run-local merged xcell/hcell lists after run-directory backup."""
    if not settings.blocked_cells:
        return
    assert settings.xcell_file is not None
    assert settings.hcell_file is not None
    _write_merged_cell_file(
        settings.xcell_file,
        settings.base_xcell_file,
        settings.blocked_cells,
        xcell=True,
    )
    _write_merged_cell_file(
        settings.hcell_file,
        settings.base_hcell_file,
        settings.blocked_cells,
        xcell=False,
    )


def validate_xrc_block_cells(
    cells: tuple[str, ...],
    *,
    base_xcell_file: Path | None,
    base_hcell_file: Path | None,
) -> None:
    _uncovered_block_cells(base_xcell_file, cells, xcell=True)
    _uncovered_block_cells(base_hcell_file, cells, xcell=False)


def _write_merged_cell_file(
    output: Path,
    base: Path | None,
    cells: tuple[str, ...],
    *,
    xcell: bool,
) -> None:
    missing = _uncovered_block_cells(base, cells, xcell=xcell)
    text = base.read_text(encoding="utf-8", errors="replace") if base else ""
    if text and not text.endswith("\n"):
        text += "\n"
    if xcell:
        text += "".join(f"{cell}\t{cell}\t-I\n" for cell in missing)
    else:
        text += "".join(f"{cell}\t{cell}\n" for cell in missing)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")


def _uncovered_block_cells(
    base: Path | None,
    cells: tuple[str, ...],
    *,
    xcell: bool,
) -> tuple[str, ...]:
    records = _xrc_cell_records(base, xcell=xcell)
    missing: list[str] = []
    for cell in cells:
        covered = False
        for layout_pattern, source_pattern, flags in records:
            if not fnmatchcase(cell, layout_pattern):
                continue
            source_pattern = source_pattern.strip("[]")
            compatible_source = fnmatchcase(cell, source_pattern)
            compatible_flag = not xcell or (
                "-I" in flags and "-NOBLOCK" not in flags
            )
            if not compatible_source or not compatible_flag:
                kind = "xcell" if xcell else "hcell"
                raise ValueError(
                    f"Calibre XRC Block Cell {cell!r} conflicts with foundry "
                    f"{kind} mapping: {layout_pattern} {source_pattern} "
                    f"{' '.join(sorted(flags))}"
                )
            covered = True
        if not covered:
            missing.append(cell)
    return tuple(missing)


def _xrc_cell_records(
    path: Path | None,
    *,
    xcell: bool,
) -> list[tuple[str, str, frozenset[str]]]:
    if path is None:
        return []
    records: list[tuple[str, str, frozenset[str]]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        tokens = line.split()
        if not tokens:
            continue
        layout_pattern = tokens[0]
        has_source = len(tokens) > 1 and not tokens[1].startswith("-")
        source_pattern = tokens[1] if has_source else layout_pattern
        flag_start = 2 if has_source else 1
        flags = (
            frozenset(token.upper() for token in tokens[flag_start:])
            if xcell
            else frozenset()
        )
        records.append((layout_pattern, source_pattern, flags))
    return records
