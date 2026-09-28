"""Validate SQLite evidence in private copies, including every committed WAL frame."""

import hashlib
import os
import sqlite3
import struct
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .filesystem import fingerprint


def copy_checked(tree, entry, target):
    with tree.open(entry["path"]) as source:
        before = fingerprint(os.fstat(source.fileno()))
        if before != entry["identity"] or "sha256" not in entry:
            raise ValueError("SQLite evidence changed before snapshot")
        digest, remaining = hashlib.sha256(), before["size"]
        with target.open("xb") as output:
            target.chmod(0o600)
            while remaining:
                chunk = source.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise ValueError("SQLite evidence truncated during snapshot")
                digest.update(chunk)
                output.write(chunk)
                remaining -= len(chunk)
        if (fingerprint(os.fstat(source.fileno())) != before
                or digest.hexdigest() != entry["sha256"]):
            raise ValueError("SQLite evidence changed during snapshot")


def checksum(data, byte_order, state=(0, 0)):
    first, second = state
    for left, right in struct.iter_unpack(byte_order + "II", data):
        first = (first + left + second) & 0xffffffff
        second = (second + right + first) & 0xffffffff
    return first, second


def validate_wal(database, wal):
    """SQLite can ignore a corrupt WAL suffix; migration must reject it explicitly."""
    if not wal.exists() or wal.stat().st_size == 0:
        return
    with database.open("rb") as stream:
        header = stream.read(100)
    if len(header) != 100 or header[:16] != b"SQLite format 3\0":
        raise ValueError("Invalid SQLite header")
    page_size = int.from_bytes(header[16:18], "big")
    page_size = 65536 if page_size == 1 else page_size
    with wal.open("rb") as stream:
        raw = stream.read(32)
        if len(raw) != 32:
            raise ValueError("Truncated SQLite WAL header")
        magic, version, size, _sequence, salt1, salt2, c1, c2 = struct.unpack(">8I", raw)
        if (magic not in {0x377f0682, 0x377f0683} or version != 3007000
                or size != page_size or not 512 <= size <= 65536 or size & (size - 1)):
            raise ValueError("Unsupported SQLite WAL header")
        order = "<" if magic == 0x377f0682 else ">"
        state = checksum(raw[:24], order)
        if state != (c1, c2):
            raise ValueError("SQLite WAL header checksum mismatch")
        committed = False
        while True:
            frame = stream.read(24 + size)
            if not frame:
                break
            if len(frame) != 24 + size:
                raise ValueError("Truncated SQLite WAL frame")
            page, commit_size, s1, s2, c1, c2 = struct.unpack(">6I", frame[:24])
            if not page or (s1, s2) != (salt1, salt2):
                raise ValueError("SQLite WAL frame identity mismatch")
            state = checksum(frame[24:], order, checksum(frame[:8], order, state))
            if state != (c1, c2):
                raise ValueError("SQLite WAL frame checksum mismatch")
            committed = commit_size != 0
        if not committed:
            raise ValueError("SQLite WAL has no final committed transaction")


@contextmanager
def snapshot(tree, entry, wal=None):
    with tempfile.TemporaryDirectory(prefix="sico-migration-sqlite-", dir="/tmp") as temporary:
        path = Path(temporary) / "snapshot.sqlite"
        copy_checked(tree, entry, path)
        sidecar = path.with_name(path.name + "-wal")
        if wal is not None:
            copy_checked(tree, wal, sidecar)
        validate_wal(path, sidecar)
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
        try:
            deadline = time.monotonic() + 10
            connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("SQLite snapshot failed integrity validation")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("SQLite snapshot contains broken references")
            yield connection
        finally:
            connection.close()
