"""Immutable catalog results returned by the domain manager."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .catalog import Catalog


@dataclass(frozen=True)
class CatalogDiagnostics:
    """Non-fatal context collected for one manager request."""

    cds_library_file: Path
    provider: str
    authoritative: bool
    messages: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalogResult:
    """A catalog plus stable provider diagnostics."""

    catalog: Catalog
    diagnostics: CatalogDiagnostics

    @property
    def authoritative(self) -> bool:
        return self.catalog.authoritative

    @property
    def provider(self) -> str:
        return self.catalog.provider
