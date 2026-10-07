"""Validate source paths and record explicit source-file provenance."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

from .workspace import sha256_file


def qualification_has_symlink_component(path: Path) -> bool:
    if not path.is_absolute():
        return True
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def qualification_has_symlink_below(root: Path, path: Path) -> bool:
    """Check only source-root components, allowing a deployment alias above root."""

    try:
        relative = path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except (OSError, ValueError):
        return True
    current = root.resolve(strict=False)
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def qualification_safe_root(value: str | Path) -> Path:
    path = Path(value).expanduser()
    # A stable system alias may itself be a symlink.  The
    # source root must not be a symlink, but canonical ancestors are allowed;
    # this keeps the real amsVerify deployment usable while still rejecting a
    # substituted LDO_IP directory.
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ValueError("source root must be an existing absolute non-symlink directory")
    resolved = path.resolve(strict=True)
    if "xxxx28" in str(resolved).casefold():
        raise ValueError("source root is in the excluded workspace")
    return resolved


def qualification_safe_child(root: Path, relative: str, *, kind: str) -> Path:
    candidate = root / relative
    if candidate.is_symlink():
        raise ValueError("%s contains a symlink: %s" % (kind, relative))
    # root is already canonical.  Reject symlinks in the source-relative
    # portion, while not treating an external mount alias as an escape.
    current = root
    for part in Path(relative).parts:
        current = current / part
        try:
            if current.is_symlink():
                raise ValueError("%s contains a symlink: %s" % (kind, relative))
        except OSError as exc:
            raise ValueError("cannot inspect %s: %s" % (kind, relative)) from exc
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root) or not candidate.exists():
        raise ValueError("%s is missing or escaped source root: %s" % (kind, relative))
    if kind == "file" and (not resolved.is_file() or not stat.S_ISREG(resolved.stat().st_mode)):
        raise ValueError("source view is not a regular file: %s" % relative)
    if kind == "directory" and not resolved.is_dir():
        raise ValueError("source path is not a directory: %s" % relative)
    return resolved


def qualification_file_record(path: Path, root: Path) -> dict[str, Any]:
    info = path.stat()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "size": info.st_size,
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "kind": "file",
    }


def qualification_external_file_record(path: Path) -> dict[str, Any]:
    info = path.stat()
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "size": info.st_size,
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "kind": "file",
    }
