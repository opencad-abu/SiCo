"""Disposable journal index serialization; every cache byte is checksummed."""

import hashlib
import os
from pathlib import Path
import tempfile

from .journal import open_private
from .journal_records import MAX_RECORD

INDEX_CONTRACT = "cadai.journal.index.v3"
INDEX_NAME = "events.idx"


def save_index(index, directory):
    """Publish a fully covered snapshot atomically, with one temp file per writer.

    Cache durability is optional: interruption can cost a rebuild, never journal
    facts. Partial snapshots are not published as reusable caches.
    """
    if index.fingerprint is None or index.covered != index.fingerprint[2]:
        return False
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=INDEX_NAME + ".", suffix=".tmp", dir=directory)
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as stream:
            stamp = " ".join(str(part) for part in index.fingerprint)
            header = f"{INDEX_CONTRACT} {stamp} {index.committed}\n".encode("ascii")
            stream.write(header)
            digest.update(header)
            for entry in zip(index.sequences, index.offsets, index.lengths, index.flags):
                line = (" ".join(map(str, entry)) + "\n").encode("ascii")
                stream.write(line)
                digest.update(line)
            stream.write(f"sha256 {digest.hexdigest()}\n".encode("ascii"))
        os.replace(temporary, Path(directory) / INDEX_NAME)
        return True
    except (OSError, ValueError):
        return False
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def load_index(factory, directory, fingerprint):
    """Accept only intact, contiguous caches of the exact current file.

    v2 and stale fingerprints are deliberately discarded: growth alone cannot
    distinguish an append from an in-place rewrite of the historical prefix.
    The digest detects accidental cache damage, not maliciously re-signed data.
    """
    try:
        fd = open_private(Path(directory) / INDEX_NAME, os.O_RDONLY)
        with os.fdopen(fd, "rb") as stream:
            return _read_index(factory, stream, fingerprint)
    except (OSError, ValueError):
        return None, False


def _read_index(factory, stream, fingerprint):
    header = stream.readline(512)
    parts = header.split()
    if (not header.endswith(b"\n") or len(parts) != 7
            or parts[0] != INDEX_CONTRACT.encode("ascii")
            or tuple(map(int, parts[1:6])) != fingerprint):
        return None, False
    count = int(parts[6])
    if not 0 <= count <= fingerprint[2]:
        return None, False
    index = factory(fingerprint)
    digest = hashlib.sha256(header)
    for sequence in range(1, count + 1):
        line = stream.readline(128)
        fields = line.split()
        if not line.endswith(b"\n") or len(fields) != 4:
            return None, False
        number, offset, length, flags = map(int, fields)
        if (number != sequence or offset != index.covered
                or not 0 < length <= MAX_RECORD or not 0 <= flags <= 255
                or offset + length > fingerprint[2]):
            return None, False
        index.add(number, offset, length, flags)
        digest.update(line)
    footer = f"sha256 {digest.hexdigest()}\n".encode("ascii")
    if (index.covered != fingerprint[2] or stream.readline(128) != footer
            or stream.read(1)):
        return None, False
    return index, True
