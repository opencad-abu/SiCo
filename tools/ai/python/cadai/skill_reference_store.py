"""SQLite and dependency-free JSON stores for the SKILL API reference."""

from __future__ import annotations

import importlib
import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

DATABASE_NAME = "skill_api.sqlite3"
FALLBACK_NAME = "skill_api.json.gz"
FALLBACK_SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})
MAX_FALLBACK_BYTES = 128 * 1024 * 1024
MAX_FALLBACK_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_FALLBACK_FUNCTIONS = 100_000

_V1_FIELDS = (
    "reference_id",
    "name",
    "usage",
    "doc_set",
    "document",
    "doc_title",
    "product_version",
    "body",
)
_V2_FIELDS = _V1_FIELDS + (
    "description",
    "arguments_json",
    "returns_json",
    "examples_json",
    "related_json",
    "kind",
    "domain",
    "family",
    "source_kind",
)
_SEARCH_FIELDS = ("name", "reference_id", "usage", "doc_title", "description", "body")
_UNAVAILABLE = (
    "Cadence SKILL reference data is unavailable in this installation; set "
    "SICO_AI_SKILL_REFERENCE_ROOT to an authorized directory containing "
    "skill_api.sqlite3 or skill_api.json.gz, or use cds_help."
)


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
        # MCP 工具桥按请求分配线程；连接跨线程共享，查询串行化。
        self._lock = threading.RLock()
        self._sqlite: Any = None
        self._connection: Any = None
        self._records: list[dict[str, str]] | None = None
        self._metadata: dict[str, Any] | None = None
        self._version: int | None = None
        self._columns: set[str] = set()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
            self._sqlite = None
            self._connection = None
            self._records = None
            self._metadata = None
            self._version = None
            self._columns = set()

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

    def search(
        self, tokens: list[str], filters: Mapping[str, str] | None = None
    ) -> list[Mapping[str, str]]:
        self._ensure_open()
        filters = {key: value for key, value in (filters or {}).items() if value}
        if filters and not set(filters).issubset(self._columns):
            return []
        if self._connection is not None:
            clauses = []
            parameters: list[str] = []
            fields = list(_SEARCH_FIELDS)
            if self._version == 2:
                fields.extend(("arguments_json", "examples_json", "related_json"))
            for token in tokens:
                clauses.append("(" + " OR ".join(
                    f"{field} LIKE ? ESCAPE '\\'" for field in fields
                ) + ")")
                parameters.extend([_like_pattern(token)] * len(fields))
            for field in ("kind", "domain", "family"):
                if field in filters:
                    clauses.append(f"{field} = ? COLLATE NOCASE")
                    parameters.append(filters[field])
            statement = "SELECT * FROM functions WHERE " + " AND ".join(clauses)
            return self._query(statement, parameters)

        assert self._records is not None
        normalized = [token.casefold() for token in tokens]
        matches = []
        for record in self._records:
            values = tuple(record.get(field, "").casefold() for field in _SEARCH_FIELDS)
            filtered = all(
                record.get(field, "").casefold() == value.casefold()
                for field, value in filters.items()
            )
            if filtered and all(any(token in value for value in values) for token in normalized):
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
        """Serialize one query; a broken connection is dropped and reopened once."""
        with self._lock:
            for attempt in (0, 1):
                self._ensure_open()
                if self._connection is None:
                    raise SkillReferenceUnavailable(
                        "SKILL API database is unavailable: " + str(self.database)
                        + ". " + _UNAVAILABLE
                    )
                try:
                    return list(self._connection.execute(statement, parameters))
                except self._sqlite.Error as exc:
                    # 连接可被其他使用方破坏（关闭、线程边界、重建）：丢弃后按需重开。
                    self.close()
                    if attempt:
                        raise SkillReferenceUnavailable(
                            f"cannot query SKILL API database: {exc}"
                        ) from exc
            return []

    def _ensure_open(self) -> None:
        with self._lock:
            if self._metadata is not None:
                return
            try:
                sqlite = _import_sqlite()
            except ImportError as exc:
                self._load_fallback(f"Python sqlite3 is unavailable: {exc}")
                return
            if not self.database.is_file():
                if self.fallback.is_file():
                    self._load_fallback(
                        f"SKILL API database is unavailable: {self.database}"
                    )
                    return
                raise SkillReferenceUnavailable(
                    f"SKILL API database is unavailable: {self.database}. "
                    + _UNAVAILABLE
                )
            self._open_sqlite(sqlite)

    def _open_sqlite(self, sqlite: Any) -> None:
        connection = None
        try:
            path = self.database.resolve(strict=True)
            uri = f"file:{quote(str(path), safe='/')}?mode=ro"
            # 只读共享连接：调用方通过 self._lock 串行化，允许跨线程复用。
            connection = sqlite.connect(uri, uri=True, check_same_thread=False)
            connection.row_factory = sqlite.Row
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in SUPPORTED_SCHEMA_VERSIONS:
                raise ValueError(f"unsupported SKILL API database schema: {version}")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(functions)")}
            required = _V2_FIELDS if version == 2 else _V1_FIELDS
            if not set(required).issubset(columns):
                missing = ", ".join(sorted(set(required) - columns))
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
        self._version = version
        self._columns = columns

    def _load_fallback(self, sqlite_error: str) -> None:
        if not self.fallback.is_file():
            raise SkillReferenceUnavailable(
                f"{sqlite_error}; cannot open SKILL API fallback: {self.fallback}. "
                + _UNAVAILABLE
            )
        try:
            gzip = importlib.import_module("gzip")
            zlib = importlib.import_module("zlib")
        except ImportError as exc:
            raise SkillReferenceUnavailable(
                f"{sqlite_error}; cannot open SKILL API fallback: {exc}. "
                + _UNAVAILABLE
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
            records, metadata, version = self._validate_fallback(payload)
        except (
            EOFError,
            json.JSONDecodeError,
            OSError,
            UnicodeDecodeError,
            ValueError,
            zlib.error,
        ) as exc:
            raise SkillReferenceUnavailable(
                f"{sqlite_error}; cannot open SKILL API fallback: {exc}. "
                + _UNAVAILABLE
            ) from exc
        self._records = records
        self._metadata = metadata
        self._version = version
        self._columns = set(records[0]) if records else set()

    @staticmethod
    def _validate_fallback(
        payload: Any,
    ) -> tuple[list[dict[str, str]], dict[str, Any], int]:
        if not isinstance(payload, dict) or payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError("unsupported fallback schema")
        version = payload["schema_version"]
        fields = _V2_FIELDS if version == 2 else _V1_FIELDS
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
                isinstance(raw_record.get(field), str) for field in fields
            ):
                raise ValueError("fallback function record is invalid")
            record = {field: raw_record[field] for field in fields}
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
        return records, metadata, version


__all__ = [
    "DATABASE_NAME",
    "FALLBACK_NAME",
    "FALLBACK_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "MAX_FALLBACK_BYTES",
    "MAX_FALLBACK_COMPRESSED_BYTES",
    "MAX_FALLBACK_FUNCTIONS",
    "ReferenceStore",
    "SkillReferenceUnavailable",
]
