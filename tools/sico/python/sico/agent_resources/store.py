"""Revision-checked atomic writes of project resource defaults."""

from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import uuid

from sicolock import lock as state_lock
from sicostate import project_directory

from ..storage.journal import open_private, sync_directory
from ..storage.project_locking import validate_locking
from .manifest import default_path, read_manifest
from .schema import MAX_MANIFEST, normalize_manifest, require_supported


class ResourceConflictError(ValueError):
    """The editor's revision no longer describes the saved defaults."""


class ResourceSaveUncertainError(OSError):
    """Replacement happened, but its durability could not be confirmed."""


def read_project(project):
    path = default_path(project)
    return {**read_manifest(path), "source": "project", "editable": path.parents[1].name == ".sico"}


def _check_target(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_mode & 0o022):
        raise ValueError("工程资源清单必须是当前用户所有、不可被其他用户写入的独立普通文件")


@contextmanager
def _writer(path):
    validate_locking(path.parent)
    lock_path = path.with_name(path.name + ".lock")
    fd = open_private(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        try:
            state_lock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise BlockingIOError("另一处正在保存资源配置，请稍后重试") from None
            raise
        opened, current = os.fstat(fd), lock_path.lstat()
        if (opened.st_nlink != 1 or
                (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
            raise ValueError("资源配置写入锁已更换，请重新读取配置")
        yield
    finally:
        os.close(fd)


def _replace(path, raw):
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
            raise ResourceSaveUncertainError(
                "清单已替换，目录同步未确认；请重新读取配置核对，不要直接重试保存"
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def save_project(project, manifest, *, expected_revision):
    """Save only project defaults; caller supplies the revision it actually read.

    Missing files have revision None. Read errors never become empty defaults.
    The shared lock serializes cooperating writers, including different mounts;
    operator edits observed before replacement fail the revision check.
    """
    if expected_revision is not None and (
        not isinstance(expected_revision, str)
        or re.fullmatch(r"[a-f0-9]{64}", expected_revision) is None
    ):
        raise ValueError("保存需要读取时的资源配置 revision")
    normalized = normalize_manifest(manifest)
    require_supported(normalized)
    raw = (json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2)
           + "\n").encode("utf-8")
    if len(raw) > MAX_MANIFEST:
        raise ValueError("外部资源清单超过 64 KiB")
    directory = project_directory(project, "ai", create=True)
    path = default_path(project)
    with _writer(path):
        _check_target(path)
        current = read_manifest(path)
        if current["revision"] != expected_revision:
            raise ResourceConflictError("资源配置已被修改，请重新读取后合并草稿")
        # Persist newly created ancestors before publishing the document.
        for parent in (directory, directory.parent, directory.parent.parent):
            sync_directory(parent)
        _replace(path, raw)
    return {"path": str(path), "manifest": normalized,
            "revision": hashlib.sha256(raw).hexdigest(), "source": "project", "editable": True}
