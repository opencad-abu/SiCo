from __future__ import annotations

from pathlib import Path
from types import ModuleType

from rcepy.dspf._sqlite import _load_dbapi, sqlite3
from rcepy.dspf.schema import connect_database


def test_dbapi_loader_prefers_pysqlite3() -> None:
    preferred = ModuleType("pysqlite3.dbapi2")
    imported: list[str] = []

    def importer(name: str) -> ModuleType:
        imported.append(name)
        return preferred

    assert _load_dbapi(importer) is preferred
    assert imported == ["pysqlite3.dbapi2"]


def test_dbapi_loader_falls_back_to_stdlib_sqlite3() -> None:
    fallback = ModuleType("sqlite3")
    imported: list[str] = []

    def importer(name: str) -> ModuleType:
        imported.append(name)
        if name == "pysqlite3.dbapi2":
            raise ImportError("pysqlite3 is unavailable")
        return fallback

    assert _load_dbapi(importer) is fallback
    assert imported == ["pysqlite3.dbapi2", "sqlite3"]


def test_connection_uses_selected_dbapi_and_named_rows(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "index.sqlite3")
    try:
        assert connection.row_factory is sqlite3.Row
        row = connection.execute("SELECT 1 AS value").fetchone()
        assert isinstance(row, sqlite3.Row)
        assert row["value"] == 1
    finally:
        connection.close()
