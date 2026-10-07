"""Private snapshot artifact validation and bounded export file I/O."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .snapshot_schema import MAX_SNAPSHOT_BYTES, SnapshotError


def validate_artifact(spool: Path, artifact: Mapping[str, Any]) -> tuple[Path, int, str]:
    path_value = artifact.get("path")
    size = artifact.get("size")
    digest = artifact.get("sha256")
    if (
        not isinstance(path_value, str)
        or isinstance(size, bool)
        or not isinstance(size, int)
        or not isinstance(digest, str)
    ):
        raise SnapshotError("snapshot artifact metadata is invalid")
    path = Path(path_value).expanduser()
    if not path.is_absolute() or path.parent != spool:
        raise SnapshotError("snapshot artifact is outside the session spool")
    try:
        path_info = path.lstat()
    except OSError as exc:
        raise SnapshotError("snapshot artifact is unavailable") from exc
    if (
        stat.S_ISLNK(path_info.st_mode)
        or not stat.S_ISREG(path_info.st_mode)
        or path_info.st_uid != os.getuid()
        or path_info.st_nlink != 1
        or path_info.st_mode & 0o077
    ):
        raise SnapshotError("snapshot artifact is not a private regular file")
    if size != path_info.st_size or size <= 0 or size > MAX_SNAPSHOT_BYTES:
        raise SnapshotError("snapshot artifact size is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SnapshotError("snapshot artifact sha256 is invalid")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    fd = -1
    hasher = hashlib.sha256()
    try:
        fd = os.open(path, flags)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_dev != path_info.st_dev
            or info.st_ino != path_info.st_ino
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_size != size
        ):
            raise SnapshotError("snapshot artifact changed during validation")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
    except OSError as exc:
        raise SnapshotError("snapshot artifact cannot be read safely") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    if hasher.hexdigest() != digest:
        raise SnapshotError("snapshot artifact sha256 does not match")
    return path, size, digest


def write_stream(spool: Path, prefix: str, chunks: Iterable[bytes]) -> dict[str, Any]:
    require_private_directory(spool)
    name = f"{prefix}-{secrets.token_hex(12)}.txt"
    path = spool / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise SnapshotError("export produced a non-byte chunk")
                size += len(chunk)
                if size > MAX_SNAPSHOT_BYTES:
                    raise SnapshotError("export artifact exceeds the size limit")
                handle.write(chunk)
                digest.update(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {"path": str(path), "size": size, "sha256": digest.hexdigest()}


def require_private_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise SnapshotError(f"snapshot spool is not a real directory: {path}")
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SnapshotError(f"snapshot spool is not private: {path}")
