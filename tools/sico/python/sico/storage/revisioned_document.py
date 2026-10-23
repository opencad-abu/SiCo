"""Revision-checked atomic JSON documents in project AI state."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager

from sicolock import lock as state_lock
from sicostate import project_directory

from .journal import open_private, sync_directory
from .project_locking import validate_locking
from cadai.json_values import strict_json

MAX_BYTES = 4096
REVISION = re.compile(r"[a-f0-9]{64}\Z")


class DocumentConflictError(ValueError):
    """The editor's revision no longer describes the saved document."""


class DocumentSaveUncertainError(OSError):
    """Replacement happened, but its durability could not be confirmed."""


def read(path, *, max_bytes=MAX_BYTES):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("document must be a regular file")
            raw = stream.read(max_bytes + 1)
    except FileNotFoundError:
        return None, None
    if len(raw) > max_bytes:
        raise ValueError("document exceeds size limit")
    return strict_json(raw), hashlib.sha256(raw).hexdigest()


def _check_target(path, label):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_mode & 0o022):
        raise ValueError(label + "必须是当前用户所有、不可被其他用户写入的独立普通文件")


@contextmanager
def _writer(path, label):
    validate_locking(path.parent)
    lock_path = path.with_name(path.name + ".lock")
    fd = open_private(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        try:
            state_lock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise BlockingIOError("另一处正在保存" + label + "，请稍后重试") from None
            raise
        opened, current = os.fstat(fd), lock_path.lstat()
        if (opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
            raise ValueError(label + "写入锁已更换，请重新读取")
        yield
    finally:
        os.close(fd)


def _replace(path, raw, label):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        fd = open_private(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            sync_directory(path.parent)
        except OSError as exc:
            raise DocumentSaveUncertainError(
                label + "已替换，目录同步未确认；请重新读取核对"
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def save(project, path, payload, *, expected_revision, label, max_bytes=MAX_BYTES):
    if expected_revision is not None and (
        not isinstance(expected_revision, str) or REVISION.fullmatch(expected_revision) is None
    ):
        raise ValueError("保存需要读取时的" + label + " revision")
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
           + "\n").encode("utf-8")
    if len(raw) > max_bytes:
        raise ValueError(label + "超过大小限制")
    project_directory(project, "ai", create=True)
    with _writer(path, label):
        _check_target(path, label)
        _, current = read(path, max_bytes=max_bytes)
        if current != expected_revision:
            raise DocumentConflictError(label + "已被修改，请重新读取后重试")
        _replace(path, raw, label)
    return read(path, max_bytes=max_bytes)
