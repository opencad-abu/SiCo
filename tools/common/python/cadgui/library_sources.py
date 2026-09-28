"""Physical catalog source references and change identities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
from cadview.catalog import CatalogLibrary, CatalogCell, CatalogView


CatalogSelection = Tuple[str, str, str]
_PartialSelection = Tuple[Optional[str], Optional[Tuple[str, ...]], Optional[str], Optional[str], Optional[str]]
_ObjectSelection = Tuple[Optional[CatalogLibrary], Optional[CatalogCell], Optional[CatalogView]]


@dataclass(frozen=True)
class _CellSource:
    library: CatalogLibrary
    cell: CatalogCell


@dataclass(frozen=True)
class _ViewSource:
    library: CatalogLibrary
    cell: CatalogCell
    view: CatalogView


def _library_identity(value: Optional[CatalogLibrary]):
    return None if value is None else (value.name, value.path)

def _cell_identity(
        library: Optional[CatalogLibrary],
    value: Optional[CatalogCell],
):
    if library is None or value is None:
        return None
    return (_library_identity(library), value.name, value.path)

def _view_identity(
        library: Optional[CatalogLibrary],
    cell: Optional[CatalogCell],
    value: Optional[CatalogView],
):
    if library is None or cell is None or value is None:
        return None
    return (_cell_identity(library, cell), value.name, value.path)

def _selection_identities(values: _ObjectSelection):
    library, cell, view = values
    return (
        _library_identity(library),
        _cell_identity(library, cell),
        _view_identity(library, cell, view),
    )
