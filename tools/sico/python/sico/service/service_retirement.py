"""Reclaim only a proven dead generation's marked local endpoint under the project lock."""

import os
import stat
from pathlib import Path

from ..storage.project_files import RegistryError, read_record
from .background_watchdog import process_identity
from .service_discovery import read_descriptor
from .service_runtime import validate_endpoint


def retire_endpoint(directory, project_id, host):
    # Discovery is not deletion authority: require a matching marker and dead process.
    try:
        previous = read_descriptor(directory)
        if previous.project_id != project_id or previous.host != host:
            return
        try:
            process = process_identity(previous.pid)
        except FileNotFoundError:
            process = None
        if (process is not None and process["start"] == previous.process_start
                and process["state"] not in {"Z", "X"}):
            return
        endpoint = Path(previous.endpoint)
        root = endpoint.parent
        if (root.parent != Path("/tmp") or not root.name.startswith("sico-service-")
                or endpoint.name != "socket"):
            return
        validate_endpoint(endpoint)
        root_info, endpoint_info = root.lstat(), endpoint.lstat()
        if read_record(root / "owner.json") != previous.record():
            return
        # Refuse unexpected contents and never follow a directory or record symlink.
        if {item.name for item in root.iterdir()} != {"socket", "owner.json"}:
            return
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (info.st_uid != os.getuid() or info.st_mode & 0o077
                    or (info.st_dev, info.st_ino) != (root_info.st_dev, root_info.st_ino)):
                return
            socket_info = os.stat("socket", dir_fd=fd, follow_symlinks=False)
            if (not stat.S_ISSOCK(socket_info.st_mode)
                    or socket_info.st_ino != endpoint_info.st_ino):
                return
            os.unlink("socket", dir_fd=fd)
            os.unlink("owner.json", dir_fd=fd)
            if root.lstat().st_ino == info.st_ino:
                root.rmdir()
        finally:
            os.close(fd)
    except (OSError, RegistryError, ValueError):
        # Unverifiable stale runtime is left for explicit operator cleanup.
        return
