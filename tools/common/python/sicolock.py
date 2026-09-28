"""One descriptor-owned lock protocol for local and NFS project state."""

import errno
import fcntl
import os
import platform
import struct


def _record_lock(fd, operation):
    # The supported runtime ABI is Linux x86_64. OFD locks survive unrelated
    # closes and, like flock, are released with the owning open description.
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise OSError(errno.ENOTSUP, "Shared state locks require Linux x86_64 OFD locks")
    kind = (fcntl.F_UNLCK if operation & fcntl.LOCK_UN else
            fcntl.F_WRLCK if operation & fcntl.LOCK_EX else fcntl.F_RDLCK)
    command = (fcntl.F_OFD_SETLK if operation & (fcntl.LOCK_NB | fcntl.LOCK_UN)
               else fcntl.F_OFD_SETLKW)
    fcntl.fcntl(fd, command, struct.pack("hhqqi4x", kind, os.SEEK_SET, 0, 0, 0))


def lock(fd, operation):
    """Acquire both legacy flock and the cross-mount record lock; never fall back.

    Local flock and NFS-emulated flock use different kernel lock domains. The
    OFD lock also conflicts with the NFS server's record lock, while flock keeps
    local legacy writers visible during the migration window. Callers acquire
    once per open description, then close or unlock; lock conversion is unused.
    Exclusive acquisition requires an O_RDWR descriptor on NFS.
    """
    fd = fd if isinstance(fd, int) else fd.fileno()
    if operation & fcntl.LOCK_UN:
        _record_lock(fd, operation)
        fcntl.flock(fd, operation)
        return
    fcntl.flock(fd, operation)
    try:
        _record_lock(fd, operation)
    except BaseException:
        fcntl.flock(fd, fcntl.LOCK_UN)
        raise
