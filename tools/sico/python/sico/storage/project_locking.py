"""Reject filesystems whose flock configuration cannot provide project ownership."""

import re
from pathlib import Path

from .project_files import RegistryError

LOCAL_FILESYSTEMS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "tmpfs", "ramfs",
                     "overlay", "zfs"}


def _mount_path(value):
    return Path(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value))


def mount_policy(directory):
    # Resolving here only selects the mount policy; callers retain their launch alias.
    target = directory.resolve(strict=True)
    with Path("/proc/self/mountinfo").open(encoding="utf-8") as stream:
        source = stream.read(1024 * 1024 + 1)
    if len(source) > 1024 * 1024:
        raise RegistryError("lock_unavailable", "Mount information exceeds limit")
    matches = []
    for line in source.splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
            mount = _mount_path(fields[4])
            filesystem = fields[separator + 1]
            options = set((fields[5] + "," + fields[separator + 3]).split(","))
        except (ValueError, IndexError) as exc:
            raise RegistryError("lock_unavailable", "Invalid mount information") from exc
        if mount == target or mount in target.parents:
            matches.append((len(mount.parts), filesystem, options))
    if not matches:
        raise RegistryError("lock_unavailable", "Project filesystem is unknown")
    _, filesystem, options = max(matches, key=lambda entry: entry[0])
    return filesystem, options


def validate_local_runtime(directory):
    filesystem, options = mount_policy(directory)
    if filesystem not in LOCAL_FILESYSTEMS or "ro" in options:
        raise RegistryError("unsafe_runtime", "Service socket requires a writable local filesystem")


def validate_locking(directory):
    filesystem, options = mount_policy(directory)
    supported = filesystem in LOCAL_FILESYSTEMS
    if filesystem in {"nfs", "nfs4"}:
        supported = not ({"nolock", "local_lock=all", "local_lock=flock"} & options)
    if not supported or "ro" in options:
        raise RegistryError("lock_unavailable", "Project filesystem has no verified flock policy")
