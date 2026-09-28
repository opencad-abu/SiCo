"""Own only this service's private, local Unix listening socket."""

import os
import socket
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ..storage.project_files import RegistryError
from ..storage.project_locking import validate_local_runtime


def validate_endpoint(path):
    path = Path(path)
    for parent in reversed(path.parents):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}
                or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)):
            raise RegistryError("unsafe_runtime", "Unsafe service runtime ancestor")
    parent, info = path.parent.lstat(), path.lstat()
    if (parent.st_uid != os.getuid() or parent.st_mode & 0o077
            or not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise RegistryError("unsafe_endpoint", "Expected a private owned service socket")
    validate_local_runtime(path.parent)


@contextmanager
def listening_socket():
    # Do not use TMPDIR/XDG_RUNTIME_DIR: host launchers can point them at project NAS.
    root = Path("/tmp")
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
            or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)):
        raise RegistryError("unsafe_runtime", "Unsafe local temporary directory")
    validate_local_runtime(root)
    directory = Path(tempfile.mkdtemp(prefix="sico-service-", dir=str(root)))
    endpoint = directory / "socket"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(endpoint))
            endpoint.chmod(0o600)
            validate_endpoint(endpoint)
            listener.listen(32)
            yield listener, str(endpoint)
    finally:
        endpoint.unlink(missing_ok=True)
        (directory / "owner.json").unlink(missing_ok=True)
        directory.rmdir()
