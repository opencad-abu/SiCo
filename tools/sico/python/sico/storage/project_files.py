"""Private, bounded records for the project service registry (backend I/O only)."""

import json
import os
import stat
import uuid
from pathlib import Path

from ..transport.framing import strict_json
from .journal import open_private, private_dir, sync_directory
from .roots import state_root

MAX_RECORD = 16384


class RegistryError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _nested_directory(project, names, *, create):
    # Keep the caller's NAS alias; neither cwd guessing nor resolve() is allowed.
    if not Path(project).expanduser().is_dir():
        raise RegistryError("project_unavailable", "Project directory is unavailable")
    try:
        directory = state_root(project, create=create)
    except ValueError as exc:
        raise RegistryError("unsafe_directory", "Invalid project registry directory: " + str(exc)) from exc
    for name in (None, *names):
        if name is not None:
            directory = directory / name
        if create and name is not None:
            directory.mkdir(mode=0o700, exist_ok=True)
        try:
            info = directory.lstat()
        except FileNotFoundError:
            return None
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & (0o077 if name in {"agent", "service", "desktop"} else 0o022)):
            raise RegistryError("unsafe_directory", "Invalid project registry directory")
    if create:
        # Persist new ancestors as well as the leaf before publishing ownership.
        for parent in (directory, *tuple(directory.parents)[:4]):
            sync_directory(parent)
    return directory


def registry_directory(project, *, create=False):
    """Ownership records of the project service (`.sico/ai/agent/service`)."""

    return _nested_directory(project, ("ai", "agent", "service"), create=create)


def window_directory(project, *, create=False):
    """Ownership lock of the one SiCo window this project allows (`.sico/ai/agent/desktop`)."""

    return _nested_directory(project, ("ai", "agent", "desktop"), create=create)


def record_fd(path, flags):
    fd = open_private(path, flags)
    if os.fstat(fd).st_nlink != 1:
        os.close(fd)
        raise RegistryError("unsafe_record", "Registry records must not have hard links")
    return fd


def read_record(path):
    with os.fdopen(record_fd(path, os.O_RDONLY), "rb") as stream:
        raw = stream.read(MAX_RECORD + 1)
    if len(raw) > MAX_RECORD:
        raise RegistryError("record_limit", "Project registry record exceeds limit")
    return strict_json(raw)


def write_record(path, value):
    raw = (json.dumps(value, allow_nan=False, ensure_ascii=True) + "\n").encode("ascii")
    if len(raw) > MAX_RECORD:
        raise RegistryError("record_limit", "Project registry record exceeds limit")
    private_dir(path.parent)
    try:
        fd = record_fd(path, os.O_RDONLY)
    except FileNotFoundError:
        pass
    else:
        os.close(fd)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with os.fdopen(record_fd(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY),
                       "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
