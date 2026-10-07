"""SQLite and dependency-free JSON stores for the SKILL API reference."""

from __future__ import annotations

import importlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

DATABASE_NAME = "skill_api.sqlite3"
FALLBACK_NAME = "skill_api.json.gz"
FALLBACK_SCHEMA_VERSION = 1
MAX_FALLBACK_BYTES = 128 * 1024 * 1024
MAX_FALLBACK_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_FALLBACK_FUNCTIONS = 100_000

_RUNTIME_FIELDS = (
    "reference_id",
    "name",
    "usage",
    "doc_set",
    "document",
    "doc_title",
    "product_version",
    "body",
)
_SEARCH_FIELDS = ("name", "reference_id", "usage", "doc_title", "body")


class SkillReferenceUnavailable(RuntimeError):
    pass


def _import_sqlite():
    return importlib.import_module("sqlite3")


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _validated_metadata(
    function_count: Any, document_count: Any, copyrights: Any
) -> dict[str, Any]:
    if isinstance(function_count, bool) or not isinstance(function_count, int):
        raise ValueError("function_count metadata must be an integer")
    if isinstance(document_count, bool) or not isinstance(document_count, int):
        raise ValueError("document_count metadata must be an integer")
    if function_count < 0 or not 0 <= document_count <= function_count:
        raise ValueError("reference counts are inconsistent")
    if not isinstance(copyrights, list) or not all(
        isinstance(copyright, str) for copyright in copyrights
    ):
        raise ValueError("copyrights metadata must be a JSON string list")
    return {
        "function_count": function_count,
        "document_count": document_count,
        "copyrights": list(copyrights),
    }


class ReferenceStore:
    def __init__(self, root: Path):
        self.database = root / DATABASE_NAME
        self.fallback = root / FALLBACK_NAME
        self._sqlite: Any = None
        self._connection: Any = None
        self._records: list[dict[str, str]] | None = None
        self._metadata: dict[str, Any] | None = None

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
        self._sqlite = None
        self._connection = None
        self._records = None
        self._metadata = None

    @property
    def backend(self) -> str | None:
        """Return the selected storage backend after initialization.

        The value is intentionally descriptive rather than an implementation
        object so diagnostics and acceptance probes cannot accidentally expose
        a live sqlite connection.
        """
        if self._metadata is None:
            return None
        return "sqlite" if self._connection is not None else "json-fallback"

    def metadata(self) -> dict[str, Any]:
        self._ensure_open()
        assert self._metadata is not None
        return {
            "function_count": self._metadata["function_count"],
            "document_count": self._metadata["document_count"],
            "copyrights": list(self._metadata["copyrights"]),
        }

    def search(self, tokens: list[str]) -> list[Mapping[str, str]]:
        self._ensure_open()
        if self._connection is not None:
            clauses = []
            parameters: list[str] = []
            for token in tokens:
                clauses.append(
                    "(name LIKE ? ESCAPE '\\' OR reference_id LIKE ? ESCAPE '\\' "
                    "OR usage LIKE ? ESCAPE '\\' OR doc_title LIKE ? ESCAPE '\\' "
                    "OR body LIKE ? ESCAPE '\\')"
                )
                parameters.extend([_like_pattern(token)] * 5)
            statement = "SELECT * FROM functions WHERE " + " AND ".join(clauses)
            return self._query(statement, parameters)

        assert self._records is not None
        normalized = [token.casefold() for token in tokens]
        matches = []
        for record in self._records:
            values = tuple(record[field].casefold() for field in _SEARCH_FIELDS)
            if all(any(token in value for value in values) for token in normalized):
                matches.append(record)
        return matches

    def exact_reference(self, name: str) -> list[Mapping[str, str]]:
        return self._exact("reference_id", name)

    def exact_name(self, name: str) -> list[Mapping[str, str]]:
        return self._exact("name", name)

    def _exact(self, field: str, value: str) -> list[Mapping[str, str]]:
        self._ensure_open()
        if self._connection is not None:
            return self._query(
                f"SELECT * FROM functions WHERE {field} = ? COLLATE NOCASE", (value,)
            )
        assert self._records is not None
        normalized = value.casefold()
        return [record for record in self._records if record[field].casefold() == normalized]

    def _query(
        self, statement: str, parameters: tuple[str, ...] | list[str]
    ) -> list[Mapping[str, str]]:
        try:
            return list(self._connection.execute(statement, parameters))
        except self._sqlite.Error as exc:
            raise SkillReferenceUnavailable(f"cannot query SKILL API database: {exc}") from exc

    def _ensure_open(self) -> None:
        if self._metadata is not None:
            return
        try:
            sqlite = _import_sqlite()
        except ImportError as exc:
            self._load_fallback(f"Python sqlite3 is unavailable: {exc}")
            return
        if not self.database.is_file():
            raise SkillReferenceUnavailable(
                f"SKILL API database is unavailable: {self.database}"
            )
        self._open_sqlite(sqlite)

    def _open_sqlite(self, sqlite: Any) -> None:
        connection = None
        try:
            path = self.database.resolve(strict=True)
            uri = f"file:{quote(str(path), safe='/')}?mode=ro"
            connection = sqlite.connect(uri, uri=True)
            connection.row_factory = sqlite.Row
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != 1:
                raise ValueError(f"unsupported SKILL API database schema: {version}")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(functions)")}
            if not set(_RUNTIME_FIELDS).issubset(columns):
                missing = ", ".join(sorted(set(_RUNTIME_FIELDS) - columns))
                raise ValueError(f"functions table is missing columns: {missing}")
            metadata = {
                row[0]: row[1] for row in connection.execute("SELECT key, value FROM metadata")
            }
            validated = _validated_metadata(
                int(metadata["function_count"]),
                int(metadata["document_count"]),
                json.loads(metadata["copyrights"]),
            )
        except sqlite.Error as exc:
            if connection is not None:
                connection.close()
            raise SkillReferenceUnavailable(f"cannot open SKILL API database: {exc}") from exc
        except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError) as exc:
            if connection is not None:
                connection.close()
            raise SkillReferenceUnavailable(f"cannot open SKILL API database: {exc}") from exc
        self._sqlite = sqlite
        self._connection = connection
        self._metadata = validated

    def _load_fallback(self, sqlite_error: str) -> None:
        try:
            gzip = importlib.import_module("gzip")
            zlib = importlib.import_module("zlib")
        except ImportError as exc:
            raise SkillReferenceUnavailable(
                f"{sqlite_error}; cannot open SKILL API fallback: {exc}"
            ) from exc
        try:
            path = self.fallback.resolve(strict=True)
            if path.stat().st_size > MAX_FALLBACK_COMPRESSED_BYTES:
                raise ValueError("fallback exceeds the compressed size limit")
            with gzip.open(path, "rb") as handle:
                encoded = handle.read(MAX_FALLBACK_BYTES + 1)
            if len(encoded) > MAX_FALLBACK_BYTES:
                raise ValueError("fallback expands beyond the size limit")
            payload = json.loads(encoded.decode("utf-8"))
            records, metadata = self._validate_fallback(payload)
        except (
            EOFError,
            json.JSONDecodeError,
            OSError,
            UnicodeDecodeError,
            ValueError,
            zlib.error,
        ) as exc:
            raise SkillReferenceUnavailable(
                f"{sqlite_error}; cannot open SKILL API fallback: {exc}"
            ) from exc
        self._records = records
        self._metadata = metadata

    @staticmethod
    def _validate_fallback(payload: Any) -> tuple[list[dict[str, str]], dict[str, Any]]:
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != FALLBACK_SCHEMA_VERSION
        ):
            raise ValueError("unsupported fallback schema")
        raw_records = payload.get("functions")
        raw_metadata = payload.get("metadata")
        if not isinstance(raw_records, list) or len(raw_records) > MAX_FALLBACK_FUNCTIONS:
            raise ValueError("fallback functions list is invalid")
        if not isinstance(raw_metadata, dict):
            raise ValueError("fallback metadata is invalid")
        records = []
        reference_ids = set()
        for raw_record in raw_records:
            if not isinstance(raw_record, dict) or not all(
                isinstance(raw_record.get(field), str) for field in _RUNTIME_FIELDS
            ):
                raise ValueError("fallback function record is invalid")
            record = {field: raw_record[field] for field in _RUNTIME_FIELDS}
            normalized_reference_id = record["reference_id"].casefold()
            if normalized_reference_id in reference_ids:
                raise ValueError("fallback contains duplicate reference ids")
            reference_ids.add(normalized_reference_id)
            records.append(record)
        metadata = _validated_metadata(
            raw_metadata.get("function_count"),
            raw_metadata.get("document_count"),
            raw_metadata.get("copyrights"),
        )
        if metadata["function_count"] != len(records):
            raise ValueError("fallback function count does not match its records")
        return records, metadata


__all__ = [
    "DATABASE_NAME",
    "FALLBACK_NAME",
    "FALLBACK_SCHEMA_VERSION",
    "MAX_FALLBACK_BYTES",
    "MAX_FALLBACK_COMPRESSED_BYTES",
    "MAX_FALLBACK_FUNCTIONS",
    "ReferenceStore",
    "SkillReferenceUnavailable",
]
