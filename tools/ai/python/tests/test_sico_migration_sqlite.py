"""SQLite's permissive WAL recovery must not discard migration evidence silently."""

import hashlib
import sqlite3
import struct

import pytest

from sico.migration.filesystem import Tree, fingerprint
from sico.migration.sqlite_snapshot import snapshot, validate_wal


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "data.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
    connection.execute("INSERT INTO evidence VALUES ('only in committed WAL')")
    connection.commit()
    try:
        yield path
    finally:
        connection.close()


def entry(path):
    return {"path": path.name, "identity": fingerprint(path.stat()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_snapshot_reads_committed_wal_without_touching_source(database):
    wal = database.with_name(database.name + "-wal")
    before = {path: (path.read_bytes(), fingerprint(path.stat()))
              for path in database.parent.iterdir()}
    tree = Tree(database.parent)
    try:
        with snapshot(tree, entry(database), entry(wal)) as connection:
            assert connection.execute("SELECT value FROM evidence").fetchall() == [
                ("only in committed WAL",)]
            with pytest.raises(sqlite3.OperationalError):
                connection.execute("DELETE FROM evidence")
    finally:
        tree.close()
    assert before == {path: (path.read_bytes(), fingerprint(path.stat())) for path in before}


@pytest.mark.parametrize("damage", ["header", "checksum", "salt", "tail", "uncommitted"])
def test_invalid_wal_cannot_be_silently_ignored(database, tmp_path, damage):
    from sico.migration.sqlite_snapshot import checksum

    source = database.with_name(database.name + "-wal")
    wal = tmp_path / "damaged-wal"
    raw = bytearray(source.read_bytes())
    page_size = struct.unpack(">I", raw[8:12])[0]
    if damage == "header":
        raw[4] ^= 1
    elif damage == "checksum":
        raw[-1] ^= 1
    elif damage == "salt":
        raw[40] ^= 1
    elif damage == "tail":
        raw += b"partial-frame"
    else:
        start = len(raw) - 24 - page_size
        raw[start + 4:start + 8] = b"\0" * 4
        state = struct.unpack(">II", raw[24:32] if start == 32 else
                              raw[start - page_size - 8:start - page_size])
        order = "<" if raw[3] == 0x82 else ">"
        state = checksum(raw[start + 24:], order, checksum(raw[start:start + 8], order, state))
        raw[start + 16:start + 24] = struct.pack(">II", *state)
    wal.write_bytes(raw)
    with pytest.raises(ValueError):
        validate_wal(database, wal)


@pytest.mark.parametrize("changed", ["database", "wal"])
def test_changed_snapshot_source_rejected(database, changed):
    wal = database.with_name(database.name + "-wal")
    first, second = entry(database), entry(wal)
    path = database if changed == "database" else wal
    with path.open("ab") as stream:
        stream.write(b"changed")
    tree = Tree(database.parent)
    try:
        with pytest.raises(ValueError), snapshot(tree, first, second):
            pass
    finally:
        tree.close()
