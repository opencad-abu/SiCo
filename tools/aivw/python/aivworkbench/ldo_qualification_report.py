"""Atomically publish one private qualification report."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .ldo_qualification_paths import qualification_has_symlink_component
from .ldo_qualification_values import qualification_copy_json, qualification_digest

_MAX_REPORT_BYTES = 1024 * 1024


def write_ldo_qualification_report(
    report: Mapping[str, Any],
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Atomically write one private M3 report without following symlinks."""

    if not isinstance(report, Mapping):
        raise ValueError("LDO qualification report must be an object")
    payload = qualification_copy_json(report)
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if len(encoded.encode("utf-8")) > _MAX_REPORT_BYTES:
        raise ValueError("LDO qualification report exceeds size cap")
    target = Path(path).expanduser()
    if not target.is_absolute() or qualification_has_symlink_component(target.parent) or not target.parent.is_dir():
        raise ValueError("report parent must be an existing absolute non-symlink directory")
    if (target.exists() or target.is_symlink()) and not overwrite:
        raise ValueError("refusing to replace existing qualification report")
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError("qualification report target is unsafe")
    temporary = target.parent / (".%s.%s.tmp" % (target.name, qualification_digest(payload)[:16]))
    if temporary.exists() or temporary.is_symlink():
        raise ValueError("qualification report temporary target already exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(temporary), flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(str(temporary), str(target))
        else:
            os.link(str(temporary), str(target))
            os.unlink(str(temporary))
        os.chmod(str(target), 0o600)
    except BaseException:
        try:
            if temporary.is_file() and not temporary.is_symlink():
                temporary.unlink()
        except OSError:
            pass
        raise
    return target
