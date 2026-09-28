"""Lifetime ownership of the fixed project service lock inode."""

import errno
import fcntl
from sicolock import lock as state_lock
import os
import weakref

from .journal import sync_directory
from .project_files import RegistryError, record_fd
from .project_locking import validate_locking

_LOCKS = weakref.WeakSet()


def _after_fork():
    # close(), never LOCK_UN: a fork shares the parent's open file description.
    for lock in tuple(_LOCKS):
        lock.close()


os.register_at_fork(after_in_child=_after_fork)


class ProjectLock:
    """Internal backend capability; acquisition is nonblocking, filesystem I/O is not."""

    def __init__(self, directory, *, create=False):
        self._fd = -1
        self._pid = os.getpid()
        self._path = directory / "owner.lock"
        validate_locking(directory)
        if create:
            try:
                self._fd = record_fd(self._path, os.O_RDWR)
            except FileNotFoundError:
                if any((directory / name).exists() for name in ("project.json", "discovery.json")):
                    raise RegistryError("missing_lock", "Existing registry lost its ownership lock")
                try:
                    self._fd = record_fd(self._path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                except FileExistsError:
                    self._fd = record_fd(self._path, os.O_RDWR)
        else:
            self._fd = record_fd(self._path, os.O_RDWR)
        _LOCKS.add(self)
        try:
            state_lock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.check()
            if create:
                sync_directory(directory)
        except OSError as exc:
            self.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise BlockingIOError("Project service ownership is held") from exc
            raise RegistryError("lock_unavailable", "Project locking is unavailable") from exc
        except BaseException:
            self.close()
            raise

    def check(self):
        if self._fd < 0 or self._pid != os.getpid():
            raise RegistryError("ownership_lost", "Project ownership is closed or inherited")
        opened = os.fstat(self._fd)
        try:
            current = self._path.lstat()
        except FileNotFoundError as exc:
            raise RegistryError("ownership_lost", "Project lock was removed") from exc
        if ((opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                or opened.st_nlink != 1):
            raise RegistryError("ownership_lost", "Project lock inode was replaced")

    def close(self):
        if self._fd >= 0:
            fd, self._fd = self._fd, -1
            os.close(fd)
        _LOCKS.discard(self)

    def __enter__(self):
        self.check()
        return self

    def __exit__(self, *_args):
        self.close()
