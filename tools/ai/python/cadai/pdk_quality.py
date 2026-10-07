"""PDK file indexes, category evidence, model names and quality summaries."""

from __future__ import annotations

from . import pdk_enrich, pdk_files
from .pdk_data import binding
from .pdk_schema import PdkUnavailable


def file_layer(cache, ctx, library):
    """Lazily index one install directory while keeping cache ownership in the session."""
    root = pdk_files.install_root(binding(ctx, library)["library"]["resolved_path"])
    key = (None if root is None else str(root), library)
    cached = cache.get(key)
    if cached is None:
        cached = pdk_files.file_index(root, library)
        if len(cache) >= 8:
            cache.clear()
        cache[key] = cached
    return cached


def categories(capture, library, ctx):
    """Capture ddCat* data once, with the evidence-backed file fallback."""
    try:
        data = capture("categories", {"library": library}).get("data")
    except PdkUnavailable as exc:
        data = {
            "status": "unavailable",
            "items": [],
            "unassigned": [],
            "issues": [getattr(exc, "code", "category_unavailable")],
        }
    files = pdk_files.categories_from_files(
        pdk_files.install_root(binding(ctx, library)["library"]["resolved_path"]),
        library,
    )
    return pdk_enrich.effective_categories(data, library, files)


def model_name(value):
    """Return the effective CDF ``model`` default without guessing a name."""
    for item in (value.get("parameters") or {}).get("items", []):
        if item.get("name") == "model":
            default = item.get("default")
            if (
                isinstance(default, dict)
                and default.get("status") == "known"
                and isinstance(default.get("value"), str)
                and default["value"]
            ):
                return default["value"]
    return None


def quality(index, excluded_devices, view, items, categories):
    """Build the inclusion/exclusion report for every considered library cell."""
    cells = {row["identity"]["target"]["cell"] for row in items}
    excluded = [
        {
            "cell": cell,
            "reason": (row or {}).get("reason"),
            "model": (row or {}).get("model"),
            "scope": "directory",
        }
        for cell, row in sorted((excluded_devices or {}).items())
    ]
    registry = categories.get("cells") or {}
    views = index.get("library_cells") or {}
    unclassified = 0
    for cell in sorted(registry):
        if cell in cells:
            continue
        if not views:
            # Without the install-directory listing a registry-only cell cannot be
            # classified; it is counted instead of inventing a reason.
            unclassified += 1
            continue
        present = views.get(cell)
        reason = "registry_stale" if present is None or view in present else "no_symbol_view"
        excluded.append(
            {
                "cell": cell,
                "reason": reason,
                "categories": sorted(registry[cell]),
                "scope": "registry",
            }
        )
    excluded.sort(key=lambda row: (row["cell"], row["reason"]))
    reasons = {}
    for row in excluded:
        reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    return {
        "included_devices": len(cells) - len(excluded_devices or {}),
        "excluded_count": len(excluded),
        "excluded_devices": excluded[:512],
        "excluded_devices_truncated": len(excluded) > 512,
        "excluded_reasons": dict(sorted(reasons.items())),
        "registry_cells": len(registry),
        "directory_cells": len(cells),
        "registry_unclassified": unclassified,
        "sources": {
            "category_registry": categories.get("source"),
            "model_index_digest": index.get("digest"),
            "model_index_available": index.get("available"),
            "model_index_truncated": index.get("truncated"),
            "library_listing": "install_directory" if views else "unavailable",
        },
    }


def data_summary(catalog):
    """Return bounded readiness data for one validated persisted catalog."""
    if not catalog or not catalog.get("complete"):
        return None
    payload = catalog.get("payload") or {}
    coverage = payload.get("coverage") or {}
    quality_data = payload.get("quality") or {}
    return {
        "source": catalog.get("source"),
        "digest": catalog.get("digest"),
        "device_count": coverage.get("device_count"),
        "included_devices": quality_data.get("included_devices"),
        "excluded_devices": quality_data.get("excluded_count"),
        "excluded_reasons": quality_data.get("excluded_reasons"),
        "categories": coverage.get("categories"),
        "readiness": coverage.get("readiness"),
        "category_missing": coverage.get("category_missing"),
        "limits_published": coverage.get("limits_published"),
        "model_decks_published": coverage.get("model_decks_published"),
    }
