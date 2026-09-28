"""Immutable catalog values and stable query/serialization methods."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from .catalog_errors import CatalogError


CATALOG_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CatalogView:
    """One view directory belonging to a cell."""

    name: str
    path: Path

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "path": str(self.path)}


@dataclass(frozen=True)
class CatalogCell:
    """One cell and its discovered views."""

    name: str
    path: Path
    views: tuple[CatalogView, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "views": [view.to_dict() for view in self.views],
        }


@dataclass(frozen=True)
class CatalogCategory:
    """One Library Manager cell category.

    Category membership is metadata owned by the OA library.  The catalog
    keeps cell names rather than duplicating :class:`CatalogCell` objects so
    selecting a category can filter the existing cell column without
    changing the ``library/cell/view`` identity used by callers.  ``children``
    preserves hierarchical ``ddCat`` categories when a PDK defines them.
    """

    name: str
    members: tuple[str, ...] = ()
    children: tuple["CatalogCategory", ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "members": list(self.members),
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(frozen=True)
class CatalogCombineGroup:
    """A virtual ``ASSIGN ... COMBINE`` library relationship."""

    name: str
    members: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "members": list(self.members)}


@dataclass(frozen=True)
class CatalogLibrary:
    """One library and its discovered cells.

    ``path`` is the physical read path.  ``write_path`` is optional because a
    read-only library may not have a write location exposed by the provider.
    """

    name: str
    path: Path
    writable: bool
    cells: tuple[CatalogCell, ...] = ()
    write_path: Path | None = None
    # Direct children reported by ``ddGetCombineValue``.  The field is
    # optional and appended after the historical constructor arguments so
    # existing callers remain source-compatible.
    combine_members: tuple[str, ...] = ()
    categories: tuple[CatalogCategory, ...] = ()

    @property
    def read_path(self) -> Path:
        """Alias that makes the read/write distinction explicit to callers."""

        return self.path

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "path": str(self.path),
            "read_path": str(self.path),
            "writable": self.writable,
            "cells": [cell.to_dict() for cell in self.cells],
            "combine_members": list(self.combine_members),
            "categories": [category.to_dict() for category in self.categories],
        }
        # Keep an explicit null so a writable library with no DD write path
        # round-trips without silently changing its session semantics.
        payload["write_path"] = None if self.write_path is None else str(self.write_path)
        return payload


@dataclass(frozen=True)
class Catalog:
    """A versioned immutable source or target catalog."""

    cds_library_file: Path
    libraries: tuple[CatalogLibrary, ...] = ()
    authoritative: bool = False
    provider: str = "filesystem"
    schema_version: int = CATALOG_SCHEMA_VERSION
    combine_groups: tuple[CatalogCombineGroup, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != CATALOG_SCHEMA_VERSION:
            raise CatalogError(
                f"unsupported catalog schema version: {self.schema_version}"
            )
        if not self.provider.strip():
            raise CatalogError("catalog provider must not be empty")

    @property
    def is_authoritative(self) -> bool:
        """Compatibility spelling for consumers that use a predicate name."""

        return self.authoritative

    def library(self, name: str) -> CatalogLibrary | None:
        """Return a library by name without making ordering assumptions."""

        return next((item for item in self.libraries if item.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "authoritative": self.authoritative,
            "provider": self.provider,
            "cds_library_file": str(self.cds_library_file),
            "libraries": [library.to_dict() for library in self.libraries],
            "combine_groups": [group.to_dict() for group in self.combine_groups],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, cds_library_file=None,
                  authoritative=None, provider=None) -> "Catalog":
        """Decode the versioned protocol, preserving this catalog subtype."""
        from .catalog_decode import decode_catalog

        return decode_catalog(payload, cds_library_file=cds_library_file,
                              authoritative=authoritative, provider=provider,
                              catalog_type=cls)


CatalogSnapshot = Catalog
LibraryInfo = CatalogLibrary
CellInfo = CatalogCell
ViewInfo = CatalogView
CategoryInfo = CatalogCategory
CombineGroupInfo = CatalogCombineGroup
