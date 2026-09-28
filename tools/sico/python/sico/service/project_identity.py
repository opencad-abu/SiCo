"""Persistent project identity bound to its data directory, independent of path aliases."""

import re
import uuid
from dataclasses import asdict, dataclass

from ..storage.project_files import RegistryError, read_record, write_record

PROTOCOL = "sico_project_identity.v1"


def valid_id(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{32}", value) is not None


@dataclass(frozen=True)
class ProjectIdentity:
    project_id: str
    directory_inode: int
    directory_device: int
    lock_inode: int
    created_host: str

    def __post_init__(self):
        if (not valid_id(self.project_id) or not valid_id(self.created_host)
                or type(self.directory_inode) is not int or self.directory_inode <= 0
                or type(self.directory_device) is not int or self.directory_device < 0
                or type(self.lock_inode) is not int or self.lock_inode <= 0):
            raise RegistryError("invalid_identity", "Invalid project identity")

    def matches(self, directory, host_id):
        info = directory.parent.stat()
        return self.matches_location(info.st_ino, info.st_dev, host_id)

    def matches_location(self, inode, device, host_id):
        """Compare a live or descriptor-relative directory observation."""
        # NAS mount device numbers may differ between hosts; inode must be stable.
        return (inode == self.directory_inode
                and (host_id != self.created_host or device == self.directory_device))

    def verify(self, directory, host_id):
        if not self.matches(directory, host_id):
            raise RegistryError("project_copied", "Project identity belongs to another directory; "
                                "explicit copy initialization is required")
        if (directory / "owner.lock").lstat().st_ino != self.lock_inode:
            raise RegistryError("ownership_lost", "Project lock inode was replaced")

    def record(self):
        return dict(protocol=PROTOCOL, **asdict(self))


def read_identity(directory):
    row = read_record(directory / "project.json")
    if (row.pop("protocol", None) != PROTOCOL
            or set(row) != {"project_id", "directory_inode", "directory_device",
                            "lock_inode", "created_host"}):
        raise RegistryError("invalid_identity", "Invalid project identity schema")
    return ProjectIdentity(**row)


def create_identity(directory, host_id):
    """Only the project lock owner may call this, including explicit copy initialization."""
    info = directory.parent.stat()
    identity = ProjectIdentity(uuid.uuid4().hex, info.st_ino, info.st_dev,
                               (directory / "owner.lock").lstat().st_ino, host_id)
    write_record(directory / "project.json", identity.record())
    return identity
