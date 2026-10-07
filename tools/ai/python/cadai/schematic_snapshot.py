"""Session snapshot/index lifecycle with compatibility exports for schema values."""

from __future__ import annotations

import importlib
import json
import os
import re
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .snapshot_artifact import require_private_directory as _require_private_directory
from .snapshot_artifact import validate_artifact as _validate_artifact
from .snapshot_artifact import write_stream as _write_stream
from .snapshot_index import create_schema as _create_schema
from .snapshot_index import index_jsonl as _index_jsonl
from .snapshot_query import decode_cursor as _decode_cursor
from .snapshot_query import encode_cursor as _encode_cursor
from .snapshot_query import like_prefix as _like_prefix
from .snapshot_query import text_chunks as _text_chunks
from .snapshot_query import validate_entity as _validate_entity
from .snapshot_query import validate_limit as _validate_limit
from .snapshot_schema import MAX_QUERY_LIMIT as MAX_QUERY_LIMIT
from .snapshot_schema import MAX_SNAPSHOT_BYTES as MAX_SNAPSHOT_BYTES
from .snapshot_schema import MAX_SNAPSHOT_LINE_BYTES as MAX_SNAPSHOT_LINE_BYTES
from .snapshot_schema import SNAPSHOT_SCHEMA_VERSION as SNAPSHOT_SCHEMA_VERSION
from .snapshot_schema import SnapshotError as SnapshotError
from .snapshot_schema import SnapshotNotFound as SnapshotNotFound

_SNAPSHOT_ID = re.compile(r"snap_[0-9a-f]{64}\Z")


def _import_sqlite():
    """Import SQLite only when a snapshot operation actually needs it."""
    return importlib.import_module("sqlite3")


@dataclass
class _Snapshot:
    snapshot_id: str
    artifact: dict[str, Any]
    database: Path
    summary: dict[str, Any]
    connection: Any


class SchematicSnapshotStore:
    """Own snapshot indexes for one MCP process and one private spool."""

    def __init__(self, spool: Path):
        _require_private_directory(spool)
        self.spool = spool.resolve(strict=True)
        try:
            self._sqlite = _import_sqlite()
        except ImportError as exc:
            raise SnapshotError("schematic snapshot tools require Python sqlite3 support") from exc
        self._snapshots: dict[str, _Snapshot] = {}

    def close(self) -> None:
        for snapshot in self._snapshots.values():
            snapshot.connection.close()
            snapshot.database.unlink(missing_ok=True)
        self._snapshots.clear()

    def register(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and index one controller-produced JSONL artifact."""
        path, size, digest = _validate_artifact(self.spool, artifact)
        snapshot_id = f"snap_{digest}"
        existing = self._snapshots.get(snapshot_id)
        if existing is not None:
            if Path(existing.artifact["path"]) != path:
                path.unlink(missing_ok=True)
            return _public_snapshot(existing)

        database = self.spool / f".{snapshot_id}-{secrets.token_hex(8)}.sqlite3"
        connection: Any = None
        try:
            connection = self._sqlite.connect(database)
            os.chmod(database, 0o600)
            _create_schema(connection)
            summary = _index_jsonl(path, connection)
            summary["snapshot_id"] = snapshot_id
            summary["artifact_size"] = size
            summary["artifact_sha256"] = digest
            connection.commit()
            snapshot = _Snapshot(
                snapshot_id=snapshot_id,
                artifact={"path": str(path), "size": size, "sha256": digest},
                database=database,
                summary=summary,
                connection=connection,
            )
            self._snapshots[snapshot_id] = snapshot
            return _public_snapshot(snapshot)
        except BaseException:
            if connection is not None:
                connection.close()
            database.unlink(missing_ok=True)
            raise

    def query(
        self,
        snapshot_id: str,
        *,
        entity: str = "all",
        name: str | None = None,
        name_prefix: str | None = None,
        hierarchy_path: str | None = None,
        net: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self._get(snapshot_id)
        _validate_entity(entity)
        _validate_limit(limit)
        if name is not None and name_prefix is not None:
            raise SnapshotError("name and name_prefix are mutually exclusive")
        offset = _decode_cursor(cursor)
        clauses: list[str] = []
        parameters: list[Any] = []
        if entity != "all":
            clauses.append("r.entity = ?")
            parameters.append(entity)
        if name is not None:
            clauses.append("r.name = ?")
            parameters.append(name)
        elif name_prefix is not None:
            clauses.append("r.name LIKE ? ESCAPE '\\'")
            parameters.append(_like_prefix(name_prefix))
        if hierarchy_path is not None:
            clauses.append("r.path = ?")
            parameters.append(hierarchy_path)
        if net is not None:
            clauses.append(
                "(r.name = ? OR EXISTS (SELECT 1 FROM connections c "
                "WHERE c.ordinal = r.ordinal AND c.net = ?))"
            )
            parameters.append(net)
            parameters.append(net)
        where = " AND ".join(clauses) or "1=1"
        rows = snapshot.connection.execute(
            "SELECT r.entity, r.name, r.path, r.payload FROM records r "
            f"WHERE {where} ORDER BY r.ordinal LIMIT ? OFFSET ?",
            [*parameters, limit + 1, offset],
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [json.loads(row[3]) for row in rows]
        next_cursor = _encode_cursor(offset + limit) if has_more else None
        return {
            "snapshot_id": snapshot_id,
            "entity": entity,
            "items": items,
            "count": len(items),
            "cursor": cursor,
            "next_cursor": next_cursor,
            "summary": snapshot.summary,
        }

    def get_item(
        self,
        snapshot_id: str,
        *,
        entity: str,
        name: str,
        hierarchy_path: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self._get(snapshot_id)
        _validate_entity(entity, allow_all=False)
        if not name:
            raise SnapshotError("name must be a non-empty string")
        clauses = ["entity = ?", "name = ?"]
        parameters: list[Any] = [entity, name]
        if hierarchy_path is not None:
            clauses.append("path = ?")
            parameters.append(hierarchy_path)
        rows = snapshot.connection.execute(
            "SELECT payload FROM records WHERE " + " AND ".join(clauses) + " ORDER BY ordinal",
            parameters,
        ).fetchall()
        if not rows:
            return {
                "snapshot_id": snapshot_id,
                "found": False,
                "entity": entity,
                "name": name,
            }
        if len(rows) > 1:
            raise SnapshotError("multiple schematic items have this name; specify hierarchy_path")
        return {
            "snapshot_id": snapshot_id,
            "found": True,
            "item": json.loads(rows[0][0]),
            "summary": snapshot.summary,
        }

    def export_text(
        self,
        snapshot_id: str,
        *,
        entity: str = "all",
        name_prefix: str | None = None,
    ) -> dict[str, Any]:
        """Write a readable, bounded text artifact without returning its body."""
        snapshot = self._get(snapshot_id)
        _validate_entity(entity)
        if name_prefix is not None and not isinstance(name_prefix, str):
            raise SnapshotError("name_prefix must be a string")
        clauses: list[str] = []
        parameters: list[Any] = []
        if entity != "all":
            clauses.append("entity = ?")
            parameters.append(entity)
        if name_prefix is not None:
            clauses.append("name LIKE ? ESCAPE '\\'")
            parameters.append(_like_prefix(name_prefix))
        where = " AND ".join(clauses) or "1=1"
        rows = snapshot.connection.execute(
            f"SELECT entity, name, path, payload FROM records WHERE {where} ORDER BY ordinal",
            parameters,
        )
        chunks: Iterable[bytes] = _text_chunks(snapshot.summary, rows)
        metadata = _write_stream(self.spool, "schematic-export", chunks)
        return {
            "snapshot_id": snapshot_id,
            "format": "text",
            "entity": entity,
            "artifact": metadata,
            "summary": snapshot.summary,
        }

    def _get(self, snapshot_id: str) -> _Snapshot:
        if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID.fullmatch(snapshot_id):
            raise SnapshotError("snapshot_id is invalid")
        try:
            return self._snapshots[snapshot_id]
        except KeyError as exc:
            raise SnapshotNotFound(f"unknown snapshot_id: {snapshot_id}") from exc


def _public_snapshot(snapshot: _Snapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "artifact": dict(snapshot.artifact),
        "summary": snapshot.summary,
    }


__all__ = [
    "MAX_QUERY_LIMIT",
    "MAX_SNAPSHOT_BYTES",
    "SNAPSHOT_SCHEMA_VERSION",
    "SchematicSnapshotStore",
    "SnapshotError",
    "SnapshotNotFound",
]
