"""Descriptor-relative, no-follow inspection of an existing private state tree."""

import fcntl
from sicolock import lock as state_lock
import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from ..storage.project_locking import validate_locking
from .classification import classify
from .native_paths import inside_home

MAX_ENTRIES = 100000
MAX_FILE_BYTES = 512 * 1024 * 1024


def fingerprint(info):
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "links": info.st_nlink}


def issue(path, code):
    return {"path": path, "code": code}


class Tree:
    def __init__(self, path, *, exclusive=False):
        validate_locking(Path(path))
        self.path = Path(path).absolute()
        self.fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        self.identity = fingerprint(os.fstat(self.fd))
        self.locks = {}
        self.lock_mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH

    def close(self):
        for fd in self.locks.values():
            os.close(fd)
        self.locks.clear()
        os.close(self.fd)

    @contextmanager
    def open(self, relative, *, writable=False):
        parts = relative.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("Invalid relative state path")
        parent = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                                | os.O_CLOEXEC, dir_fd=parent)
                os.close(parent)
                parent = child
            access = os.O_RDWR if writable else os.O_RDONLY
            fd = os.open(parts[-1], access | os.O_NOFOLLOW | os.O_NONBLOCK
                         | os.O_CLOEXEC, dir_fd=parent)
        finally:
            os.close(parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise ValueError("Invalid regular state file")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                yield stream
        finally:
            os.close(fd)

    def inspect_file(self, row, blockers):
        # NFS implements flock via byte-range locks: an exclusive lock requires
        # a writable descriptor. Only stopped migration's known lock files need
        # this access; read-only audits and all data reads remain O_RDONLY.
        writable = row["action"] == "lock" and self.lock_mode == fcntl.LOCK_EX
        with self.open(row["path"], writable=writable) as stream:
            before = fingerprint(os.fstat(stream.fileno()))
            if before != row["identity"]:
                raise ValueError("State file changed during audit")
            if row["action"] == "lock":
                if row["path"] in self.locks:
                    if fingerprint(os.fstat(self.locks[row["path"]])) != before:
                        raise ValueError("Lock inode changed during audit")
                    return
                try:
                    state_lock(stream.fileno(), self.lock_mode | fcntl.LOCK_NB)
                except BlockingIOError:
                    blockers.append(issue(row["path"], "active_writer"))
                else:
                    self.locks[row["path"]] = os.dup(stream.fileno())
                return
            if before["size"] > MAX_FILE_BYTES:
                blockers.append(issue(row["path"], "file_limit"))
                return
            digest = hashlib.sha256()
            size = 0
            while True:
                raw = stream.read(min(1024 * 1024, MAX_FILE_BYTES + 1 - size))
                if not raw:
                    break
                size += len(raw)
                digest.update(raw)
                if size > MAX_FILE_BYTES:
                    raise ValueError("State file exceeded audit limit")
            if fingerprint(os.fstat(stream.fileno())) != before or size != before["size"]:
                raise ValueError("State file changed during audit")
            row["sha256"] = digest.hexdigest()

    def inventory(self):
        rows, blockers = [], []
        root = self.identity
        if root["uid"] != os.getuid() or root["mode"] & 0o022:
            return [], [issue(".", "unsafe_directory")]

        def visit(fd, prefix=""):
            before = fingerprint(os.fstat(fd))
            for name in sorted(os.listdir(fd)):
                relative = prefix + name
                if len(rows) >= MAX_ENTRIES:
                    raise ValueError("State tree exceeded audit limit")
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                directory = stat.S_ISDIR(info.st_mode)
                action, schema = classify(relative, directory)
                row = dict(path=relative, action=action, schema=schema,
                           identity=fingerprint(info), kind="directory" if directory else "file")
                rows.append(row)
                if action == "preserve":
                    continue
                private = relative == "ai/agent" or relative.startswith("ai/agent/")
                # Only reached through a validated 0700 native home. Codex creates
                # group-readable/writable children; never widen the parent boundary.
                forbidden = 0o7002 if inside_home(relative) else (0o077 if private else 0o022)
                if info.st_uid != os.getuid() or info.st_mode & forbidden:
                    blockers.append(issue(relative, "unsafe_owner_or_mode"))
                    continue
                if stat.S_ISLNK(info.st_mode):
                    blockers.append(issue(relative, "symlink"))
                    continue
                if not directory and not stat.S_ISREG(info.st_mode):
                    row.update(action="runtime", schema="special_file", kind="special")
                    blockers.append(issue(relative, "unverified_runtime"))
                    continue
                if action in {"unknown", "review"} or schema == "unregistered_lock":
                    blockers.append(issue(relative, schema))
                if directory:
                    if action == "container":
                        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                                        | os.O_CLOEXEC, dir_fd=fd)
                        try:
                            if fingerprint(os.fstat(child)) != row["identity"]:
                                raise ValueError("State directory changed during audit")
                            visit(child, relative + "/")
                        finally:
                            os.close(child)
                    elif action == "runtime":
                        blockers.append(issue(relative, "unverified_runtime"))
                elif (action in {"retain", "identity", "archive", "lock"}
                      or schema == "native_system_asset"):
                    try:
                        self.inspect_file(row, blockers)
                    except (OSError, ValueError):
                        blockers.append(issue(relative, "unreadable_or_changed_file"))
                elif action == "runtime":
                    blockers.append(issue(relative, "unverified_runtime"))
            if fingerprint(os.fstat(fd)) != before:
                raise ValueError("State directory changed during audit")

        try:
            visit(self.fd)
        except (OSError, ValueError):
            blockers.append(issue(".", "incomplete_or_changed_inventory"))
        return rows, blockers
