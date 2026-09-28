"""Atomic DSPF index construction and source-aware cache management."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from ._ingest import ingest_file
from ._sqlite import sqlite3
from .parser import PARSER_VERSION
from ..pathutil import cad_temp_dir
from .schema import (
    SCHEMA_VERSION,
    connect_database,
    create_schema,
    create_secondary_indexes,
    get_metadata,
    set_metadata,
    validate_database,
)


_SAMPLE_SIZE = 64 * 1024
_LOCK_STALE_SECONDS = 6 * 60 * 60

class IndexCancelled(RuntimeError):
    """Raised when a caller requests cancellation during index construction."""


class IndexLocked(RuntimeError):
    """Raised when another process owns the cache build lock."""


@dataclass(frozen=True)
class IndexProgress:
    bytes_read: int
    total_bytes: int
    line: int
    records: int
    diagnostics: int = 0


@dataclass(frozen=True)
class IndexResult:
    source_path: Path
    index_path: Path
    reused: bool
    status: str
    file_size: int
    total_lines: int
    counts: dict[str, int]
    warning_count: int
    error_count: int
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.update(source_path=str(self.source_path), index_path=str(self.index_path))
        return result


def default_cache_dir(*, create=False) -> Path:
    configured = os.environ.get("RCE_DSPF_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return cad_temp_dir("rce", "dspf", create=create)


def source_fingerprint(source: str | Path) -> dict[str, Any]:
    path = _validated_source(source)
    stat = path.stat()
    digest = hashlib.blake2b(digest_size=16)
    with path.open("rb") as stream:
        digest.update(stream.read(_SAMPLE_SIZE))
        if stat.st_size > _SAMPLE_SIZE:
            stream.seek(max(0, stat.st_size - _SAMPLE_SIZE))
            digest.update(stream.read(_SAMPLE_SIZE))
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sample_hash": digest.hexdigest(),
        "parser_version": PARSER_VERSION,
        "schema_version": SCHEMA_VERSION,
    }


def index_path_for(source: str | Path, cache_dir: str | Path | None = None) -> Path:
    fingerprint = source_fingerprint(source)
    encoded = json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()
    key = hashlib.sha256(encoded).hexdigest()
    root = Path(cache_dir).expanduser() if cache_dir is not None else default_cache_dir()
    return root / f"{key}.sqlite3"


def build_index(
    source: str | Path,
    *,
    index_path: str | Path | None = None,
    cache_dir: str | Path | None = None,
    force: bool = False,
    progress: Callable[[IndexProgress], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> IndexResult:
    """Build or reuse an atomic SQLite index for a plain-text DSPF file."""
    source_path = _validated_source(source)
    fingerprint = source_fingerprint(source_path)
    if index_path is None and cache_dir is None:
        cache_dir = default_cache_dir(create=True)
    target = (
        Path(index_path).expanduser().resolve()
        if index_path is not None
        else index_path_for(source_path, cache_dir).absolute()
    )
    if target == source_path:
        raise ValueError("DSPF index path must differ from the source path")
    explicit_target = index_path is not None
    if target.exists() and explicit_target and not _is_our_index(target):
        raise FileExistsError(f"Refusing to overwrite a non-DSPF-index file: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not force and _is_reusable(target, fingerprint):
        return _result_from_database(source_path, target, reused=True)
    started = time.monotonic()
    with _build_lock(target) as recovered_lock:
        if not force and _is_reusable(target, fingerprint):
            return _result_from_database(source_path, target, reused=True)
        temporary = _temporary_path(target)
        connection = None
        try:
            connection = connect_database(temporary, bulk_load=True)
            create_schema(connection)
            set_metadata(connection, "status", "building")
            set_metadata(connection, "source_fingerprint", fingerprint)
            set_metadata(connection, "parser_version", PARSER_VERSION)
            connection.commit()
            last_report = 0.0

            def report(bytes_read: int, line_no: int) -> None:
                nonlocal last_report
                now = time.monotonic()
                if progress is not None and (now - last_report >= 0.1 or bytes_read == fingerprint["size"]):
                    progress(IndexProgress(bytes_read, fingerprint["size"], line_no, line_no))
                    last_report = now

            with connection:
                counts, total_lines, bytes_read = ingest_file(
                    source_path, connection, cancelled=cancelled,
                    on_line=report if progress is not None else None,
                )
                if source_fingerprint(source_path) != fingerprint:
                    raise RuntimeError("DSPF source changed while it was being indexed")
                try:
                    create_secondary_indexes(connection, cancelled=cancelled)
                except sqlite3.OperationalError:
                    if cancelled is not None and cancelled():
                        raise IndexCancelled("DSPF indexing cancelled") from None
                    raise
                if recovered_lock:
                    connection.execute(
                        "INSERT INTO diagnostic(severity,code,message,source_line,line_end,raw_summary) "
                        "VALUES ('warning','stale_lock_recovered','Recovered stale index lock',0,0,NULL)"
                    )
                    counts["diagnostic"] += 1
                warnings = connection.execute(
                    "SELECT count(*) FROM diagnostic WHERE severity='warning'"
                ).fetchone()[0]
                errors = connection.execute(
                    "SELECT count(*) FROM diagnostic WHERE severity='error'"
                ).fetchone()[0]
                elapsed = time.monotonic() - started
                set_metadata(connection, "status", "complete")
                set_metadata(connection, "source_path", str(source_path))
                set_metadata(connection, "file_size", fingerprint["size"])
                set_metadata(connection, "total_lines", total_lines)
                set_metadata(connection, "bytes_read", bytes_read)
                set_metadata(connection, "counts", counts)
                set_metadata(connection, "warning_count", warnings)
                set_metadata(connection, "error_count", errors)
                set_metadata(connection, "elapsed_seconds", elapsed)
            validate_database(connection, verify_foreign_keys=True)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.close()
            connection = None
            os.replace(temporary, target)
            _cleanup_sidecars(temporary)
            if progress is not None:
                progress(IndexProgress(fingerprint["size"], fingerprint["size"], total_lines, counts.get("record_count", total_lines), warnings + errors))
            return IndexResult(
                source_path, target, False, "complete", fingerprint["size"],
                total_lines, counts, warnings, errors, elapsed,
            )
        except BaseException:
            if connection is not None:
                connection.close()
            _cleanup_database(temporary)
            raise


def _validated_source(source: str | Path) -> Path:
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"DSPF source does not exist: {path}")
    with path.open("rb") as stream:
        magic = stream.read(3)
    if magic.startswith(b"\x1f\x8b") or magic.startswith(b"BZh"):
        raise ValueError("Compressed DSPF input is not supported in this release")
    return path


def _is_reusable(path: Path, fingerprint: dict[str, Any]) -> bool:
    if not path.is_file():
        return False
    try:
        connection = connect_database(path, read_only=True)
        try:
            metadata = get_metadata(connection)
            validate_database(connection)
            return metadata.get("source_fingerprint") == fingerprint
        finally:
            connection.close()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, sqlite3.Error):
        return False


def _is_our_index(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        connection = connect_database(path, read_only=True)
        try:
            return get_metadata(connection).get("schema_version") == SCHEMA_VERSION
        finally:
            connection.close()
    except (OSError, ValueError, TypeError, json.JSONDecodeError, sqlite3.Error):
        return False


def _result_from_database(source: Path, index: Path, *, reused: bool) -> IndexResult:
    connection = connect_database(index, read_only=True)
    try:
        metadata = get_metadata(connection)
        validate_database(connection)
    finally:
        connection.close()
    return IndexResult(
        source, index, reused, metadata["status"], int(metadata["file_size"]),
        int(metadata["total_lines"]), dict(metadata["counts"]),
        int(metadata["warning_count"]), int(metadata["error_count"]),
        float(metadata["elapsed_seconds"]),
    )


@contextmanager
def _build_lock(index: Path) -> Iterator[bool]:
    lock = Path(str(index) + ".lock")
    recovered = False
    for _ in range(2):
        try:
            descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(f"{os.getpid()} {time.time()}\n")
            break
        except FileExistsError:
            try:
                stale = time.time() - lock.stat().st_mtime > _LOCK_STALE_SECONDS
            except FileNotFoundError:
                continue
            if not stale:
                raise IndexLocked(f"DSPF index is already being built: {index}")
            lock.unlink(missing_ok=True)
            recovered = True
    else:  # pragma: no cover - repeated external race
        raise IndexLocked(f"Could not acquire DSPF index lock: {index}")
    try:
        yield recovered
    finally:
        lock.unlink(missing_ok=True)


def _temporary_path(target: Path) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(descriptor)
    return Path(name)


def _cleanup_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def _cleanup_database(path: Path) -> None:
    path.unlink(missing_ok=True)
    _cleanup_sidecars(path)
