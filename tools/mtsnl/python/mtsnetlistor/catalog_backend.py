"""Optional shared catalog dependency boundary."""
try:
    from cadview.catalog import Catalog, CatalogLibrary, dbaccess_catalog, filesystem_catalog
except ImportError:  # pragma: no cover - callers report the dependency error
    Catalog = object
    CatalogLibrary = object
    dbaccess_catalog = None
    filesystem_catalog = None

__all__ = ["Catalog", "CatalogLibrary", "dbaccess_catalog", "filesystem_catalog"]
