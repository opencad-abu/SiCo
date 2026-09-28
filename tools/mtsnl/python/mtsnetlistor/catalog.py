"""Compatibility catalog imports, all referencing their sole domain owner.

Remove these exports once supported GUI/controller/CLI consumers import the
source, target, result and cache administration owners directly.
"""

from .catalog_cache import clear_source_catalog_cache, source_catalog_cache_info
from .catalog_result import CatalogResult
from .catalog_source import load_source_catalog
from .catalog_target import load_target_catalog, target_library_or_error

__all__ = [
    "CatalogResult", "load_source_catalog", "load_target_catalog",
    "target_library_or_error", "clear_source_catalog_cache", "source_catalog_cache_info",
]
