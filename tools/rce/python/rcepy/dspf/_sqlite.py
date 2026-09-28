"""Select the SQLite DB-API implementation used by DSPF indexes."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Callable


_PYSQLITE_MODULE = "pysqlite3.dbapi2"
_STDLIB_MODULE = "sqlite3"


def _load_dbapi(
    importer: Callable[[str], ModuleType] = import_module,
) -> ModuleType:
    try:
        return importer(_PYSQLITE_MODULE)
    except ImportError:
        return importer(_STDLIB_MODULE)


sqlite3 = _load_dbapi()


__all__ = ["sqlite3"]
