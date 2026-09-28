"""Immutable catalog query result and cache timing diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from .catalog_backend import Catalog
from .errors import CatalogError


@dataclass(frozen=True)
class CatalogResult:
    catalog: Catalog
    authoritative: bool
    provider: str
    diagnostics: tuple[str, ...] = ()


def _cache_diagnostics(
    *,
    status: str,
    fingerprint_ms: float,
    provider_ms: float,
    total_ms: float,
    age_ms: float | None = None,
) -> tuple[str, ...]:
    values = [
        f"source_catalog_cache={status}",
        f"source_fingerprint_ms={max(0.0, fingerprint_ms):.3f}",
        f"source_provider_ms={max(0.0, provider_ms):.3f}",
        f"source_total_ms={max(0.0, total_ms):.3f}",
    ]
    if age_ms is not None:
        values.append(f"source_catalog_cache_age_ms={max(0.0, age_ms):.3f}")
    return tuple(values)


def _cached_source_result(
    result: CatalogResult,
    *,
    diagnostics: tuple[str, ...],
) -> CatalogResult:
    return replace(result, diagnostics=diagnostics)


def _cadview_catalog_import_error() -> CatalogError:
    return CatalogError(
        "cadview catalog support is unavailable; add common/python to PYTHONPATH"
    )
