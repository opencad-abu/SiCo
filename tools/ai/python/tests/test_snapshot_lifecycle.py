"""Snapshot ownership and export failure behavior across the extracted helpers."""

import sqlite3
from pathlib import Path

import pytest
from test_schematic_snapshot import _artifact, _jsonl, _private_spool

from cadai import schematic_snapshot
from cadai.schematic_snapshot import SchematicSnapshotStore, SnapshotError, SnapshotNotFound


@pytest.mark.parametrize("failure", ["index", "commit"])
def test_registration_failure_closes_index_and_allows_retry(tmp_path, monkeypatch, failure):
    connections = []

    class Connection:
        def __init__(self, path, **_kwargs):
            self.connection = sqlite3.connect(path, check_same_thread=False)
            connections.append(self.connection)

        def executescript(self, sql):
            return self.connection.executescript(sql)

        def execute(self, sql, parameters):
            return self.connection.execute(sql, parameters)

        def commit(self):
            if failure == "commit":
                raise OSError("commit failed")
            self.connection.commit()

        def close(self):
            self.connection.close()

    class SQLite:
        connect = Connection

    monkeypatch.setattr(schematic_snapshot, "_import_sqlite", lambda: SQLite)
    spool = _private_spool(tmp_path)
    store = SchematicSnapshotStore(spool)
    artifact = _artifact(
        spool, _jsonl().rsplit(b"\n", 2)[0] + b"\n" if failure == "index" else None
    )
    with pytest.raises((SnapshotError, OSError)):
        store.register(artifact)
    assert not list(spool.glob(".*.sqlite3"))
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    with pytest.raises(SnapshotNotFound):
        store.query("snap_" + artifact["sha256"])
    assert Path(artifact["path"]).is_file()
    failure = None
    registered = store.register(_artifact(spool))
    assert store.query(registered["snapshot_id"])["count"] == 4
    store.close()
    assert not list(spool.glob(".*.sqlite3"))


def test_duplicate_artifact_reuses_index_then_close_preserves_source(tmp_path):
    spool = _private_spool(tmp_path)
    store = SchematicSnapshotStore(spool)
    artifact = _artifact(spool)
    registered = store.register(artifact)
    database = list(spool.glob(".*.sqlite3"))
    assert len(database) == 1
    duplicate = spool / "duplicate.jsonl"
    duplicate.write_bytes(Path(artifact["path"]).read_bytes())
    duplicate.chmod(0o600)
    assert store.register({**artifact, "path": str(duplicate)}) == registered
    assert not duplicate.exists()
    assert list(spool.glob(".*.sqlite3")) == database
    assert store.register(artifact) == registered
    store.close()
    store.close()
    assert Path(artifact["path"]).is_file()
    assert not database[0].exists()
    with pytest.raises(SnapshotNotFound):
        store.query(registered["snapshot_id"])


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_export_failure_removes_partial_file_but_keeps_index(tmp_path, monkeypatch, failure):
    spool = _private_spool(tmp_path)
    store = SchematicSnapshotStore(spool)
    registered = store.register(_artifact(spool))

    def broken_chunks(summary, rows):
        assert summary["snapshot_id"] == registered["snapshot_id"]
        assert next(iter(rows))[1] == "M0"
        yield b"partial export\n"
        raise failure("interrupted export")

    monkeypatch.setattr(schematic_snapshot, "_text_chunks", broken_chunks)
    with pytest.raises(failure, match="interrupted export"):
        store.export_text(registered["snapshot_id"])
    assert not list(spool.glob("schematic-export-*.txt"))
    assert store.query(registered["snapshot_id"], entity="instance")["count"] == 2
    store.close()


def test_schema_aliases_preserve_error_hierarchy_and_identity():
    from cadai import snapshot_schema

    for name in (
        "SNAPSHOT_SCHEMA_VERSION",
        "MAX_SNAPSHOT_BYTES",
        "MAX_SNAPSHOT_LINE_BYTES",
        "MAX_QUERY_LIMIT",
        "SnapshotError",
        "SnapshotNotFound",
    ):
        assert getattr(schematic_snapshot, name) is getattr(snapshot_schema, name)
    assert issubclass(SnapshotNotFound, SnapshotError)
