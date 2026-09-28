"""Publish a prepared directory with Linux RENAME_NOREPLACE; never emulate clobbering."""

import ctypes
import errno
import os
import platform
import stat


def publish(source, target):
    if source.name in {"", ".", ".."} or target.name in {"", ".", ".."}:
        raise ValueError("Invalid migration publication name")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    source_parent = os.open(source.parent, flags)
    try:
        target_parent = os.open(target.parent, flags)
        try:
            before = os.stat(source.name, dir_fd=source_parent, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode) or before.st_uid != os.getuid() or before.st_mode & 0o077:
                raise ValueError("Unsafe migration candidate")
            library = ctypes.CDLL(None, use_errno=True)
            operation = getattr(library, "renameat2", None)
            arguments = (ctypes.c_int(source_parent), ctypes.c_char_p(os.fsencode(source.name)),
                         ctypes.c_int(target_parent), ctypes.c_char_p(os.fsencode(target.name)),
                         ctypes.c_uint(1))
            if operation is None:
                if platform.system() != "Linux" or platform.machine() != "x86_64":
                    raise OSError(errno.ENOSYS, "Atomic non-replacing directory publication is unavailable")
                # RHEL7 libc predates renameat2; its supported x86_64 kernel ABI is 316.
                result = library.syscall(ctypes.c_long(316), *arguments)
            else:
                result = operation(*arguments)
            if result != 0:
                error = ctypes.get_errno()
                raise OSError(error, "Atomic migration publication failed", str(target))
            after = os.stat(target.name, dir_fd=target_parent, follow_symlinks=False)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise ValueError("Published migration directory identity changed")
            os.fsync(target_parent)
            os.fsync(source_parent)
        finally:
            os.close(target_parent)
    finally:
        os.close(source_parent)
