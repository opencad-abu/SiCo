"""Publish private qualification reports atomically."""

from __future__ import annotations




import json


import os

from pathlib import Path







from .protocol import (
    ErrorCode,
    ProtocolError,
)






from .qualification_models import QualificationReport

def _has_symlink_component(path: Path) -> bool:
    """Return whether any existing component of an absolute path is a symlink.

    Qualification reports are written into an operator-selected directory.
    Checking only the final parent misses an intermediate redirect such as
    ``managed-link/subdir``.  Walk from the filesystem anchor so every
    component is checked before the atomic writer opens a temporary file.
    """

    if not path.is_absolute():
        return True
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            # An inaccessible component cannot be proven safe.
            return True
    return False


def write_qualification_report(
    report: QualificationReport,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write one private, hash-addressed qualification certificate atomically.

    The writer rejects symlink targets and pre-existing files by default.  It
    does not create run directories outside the explicitly supplied parent,
    and it never writes raw provider credentials because ``QualificationReport``
    has already crossed the recursive redaction boundary.
    """

    if not isinstance(report, QualificationReport):
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification report is invalid")
    target = Path(path).expanduser()
    if not target.is_absolute():
        raise ProtocolError(
            ErrorCode.PATH_DENIED,
            "qualification report path must be absolute",
        )
    parent = target.parent
    if (
        not parent.is_dir()
        or parent.is_symlink()
        or _has_symlink_component(parent)
    ):
        raise ProtocolError(
            ErrorCode.PATH_DENIED,
            "qualification report parent must be an existing regular directory without symlink components",
        )
    if target.exists() or target.is_symlink():
        if not overwrite:
            raise ProtocolError(
                ErrorCode.PATH_DENIED,
                "qualification report already exists",
            )
        if target.is_symlink() or not target.is_file():
            raise ProtocolError(
                ErrorCode.PATH_DENIED,
                "qualification report target is unsafe",
            )
    payload = json.dumps(
        report.to_dict(),
        ensure_ascii=True,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"
    temporary = parent / (".%s.%s.tmp" % (target.name, report.report_sha256[:16]))
    if temporary.exists() or temporary.is_symlink():
        raise ProtocolError(
            ErrorCode.PATH_DENIED,
            "qualification report temporary target already exists",
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(temporary), flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(str(temporary), str(target))
        else:
            os.link(str(temporary), str(target))
            os.unlink(str(temporary))
        os.chmod(str(target), 0o600)
    except OSError as exc:
        try:
            if temporary.is_file() and not temporary.is_symlink():
                temporary.unlink()
        except OSError:
            pass
        raise ProtocolError(
            ErrorCode.PATH_DENIED,
            "cannot write qualification report",
            {"detail": str(exc)},
        ) from exc
    return target

