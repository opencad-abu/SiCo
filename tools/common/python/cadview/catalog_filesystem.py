"""Discover preview catalog cells/views from cds.lib library definitions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping
from .catalog_model import Catalog, CatalogCell, CatalogCombineGroup, CatalogLibrary, CatalogView
from .catalog_categories import _discover_categories
from .cdslib import read_library_definitions


def filesystem_catalog(
    cds_library_file: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> Catalog:
    """Build a non-authoritative catalog by walking resolved library paths."""

    cds_path = Path(cds_library_file).expanduser().resolve()
    parsed = read_library_definitions(cds_path, environ=environ)
    definitions = parsed.libraries
    combine_groups = parsed.combine_groups

    libraries = tuple(
        _filesystem_library(
            name,
            path,
            combine_members=combine_groups.get(name, ()),
        )
        for name, path in sorted(definitions.items())
        if path.is_dir()
    )
    groups = tuple(
        CatalogCombineGroup(name, members)
        for name, members in sorted(combine_groups.items())
        if name in definitions
    )
    return Catalog(
        cds_library_file=cds_path,
        libraries=libraries,
        authoritative=False,
        provider="filesystem",
        combine_groups=groups,
    )


def _filesystem_library(
    name: str,
    path: Path,
    *,
    combine_members: tuple[str, ...] = (),
) -> CatalogLibrary:
    cells: list[CatalogCell] = []
    for cell_path in sorted(
        (item for item in path.iterdir() if item.is_dir()),
        key=lambda item: item.name,
    ):
        views = tuple(
            CatalogView(view_path.name, view_path.resolve())
            for view_path in sorted(
                (item for item in cell_path.iterdir() if item.is_dir()),
                key=lambda item: item.name,
            )
        )
        cells.append(CatalogCell(cell_path.name, cell_path.resolve(), views))
    writable = _directory_is_writable(path)
    cell_tuple = tuple(cells)
    return CatalogLibrary(
        name=name,
        path=path.resolve(),
        writable=writable,
        cells=cell_tuple,
        write_path=path.resolve() if writable else None,
        combine_members=tuple(combine_members),
        categories=_discover_categories(path.resolve(), name, cell_tuple),
    )


def _directory_is_writable(path: Path) -> bool:
    """Check both access(2) and mode bits so tests are stable when run as root."""

    mode = path.stat().st_mode
    return bool(mode & 0o222) and os.access(path, os.W_OK)
