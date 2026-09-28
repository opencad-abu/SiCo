"""Read the completed migration receipt before admitting a new project state root."""

import hashlib
import json
import os
import re
import socket
import stat
from pathlib import Path

FORMAT = "sico.state.migration.activation.v1"
MARKER = "migration.json"
SERVICE = "ai/agent/service"
DEFAULT_RECORD_LIMIT = 16 * 1024
INVENTORY_RECORD_LIMIT = 64 * 1024 * 1024


def local_host_id():
    machine = Path("/etc/machine-id").read_text(encoding="ascii").strip()
    if re.fullmatch("[a-f0-9]{32}", machine) is None:
        raise ValueError("Stable machine identity is unavailable")
    return hashlib.sha256((machine + "\0" + socket.gethostname()).encode()).hexdigest()[:32]


def directory(path, *, private=True):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & (0o7077 if private else 0o7022)):
        raise ValueError("Migration directory ownership or permissions changed")
    return info


def exists(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def read_bytes(root, relative, limit):
    """Use only descriptor-relative, private, regular files below the given root."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(part in {".", ".."} for part in parts):
        raise ValueError("Invalid migration record location")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    parent = os.open(root, flags | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=parent)
            os.close(parent)
            parent = child
            info = os.fstat(parent)
            if info.st_uid != os.getuid() or info.st_mode & 0o7077:
                raise ValueError("Unsafe migration record parent")
        fd = os.open(parts[-1], flags | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o7077 or info.st_nlink != 1 or info.st_size > limit):
                raise ValueError("Unsafe migration record")
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if len(raw) != info.st_size or (info.st_mtime_ns, info.st_ctime_ns) != (
                    after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("Migration record changed during read")
            return raw
    finally:
        os.close(parent)


def _parse_record(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("Duplicate migration record key")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("Invalid migration record number")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise ValueError("Migration record must be an object")
    return value


def record(root, relative, *, limit=DEFAULT_RECORD_LIMIT):
    return _parse_record(read_bytes(root, relative, limit))


def matches(info, expected, *, same_host):
    return (isinstance(expected, dict) and type(expected.get("inode")) is int
            and expected["inode"] == info.st_ino and expected.get("uid") == info.st_uid
            and (not same_host or expected.get("device") == info.st_dev))


def admission_payload(root):
    """Return the completed marker and one validated inventory read."""
    root = Path(root)
    legacy = root.parent / ".cad"
    has_legacy, has_marker = exists(legacy), exists(root / MARKER)
    if not has_legacy and not has_marker:
        return None
    if not has_legacy or not has_marker:
        raise ValueError("Existing legacy state requires completed migration activation")
    target_info, source_info = directory(root), directory(legacy, private=False)
    marker = record(root, MARKER)
    expected = {"format", "inventory_sha256", "source_identity", "target_identity",
                "project_identity", "history_sha256", "inventory_document_sha256"}
    digest = marker.get("inventory_sha256")
    if (set(marker) != expected or marker["format"] != FORMAT or not isinstance(digest, str)
            or re.fullmatch("[a-f0-9]{64}", digest) is None):
        raise ValueError("Invalid completed migration marker")
    identity = marker["project_identity"]
    if not isinstance(identity, dict) or not isinstance(identity.get("created_host"), str):
        raise ValueError("Invalid migrated project identity")
    same_host = identity["created_host"] == local_host_id()
    if (not matches(target_info, marker["target_identity"], same_host=same_host)
            or not matches(source_info, marker["source_identity"], same_host=same_host)):
        raise ValueError("Migration source or target directory identity changed")
    control = root.parent / ".sico-migration"
    directory(control)
    receipt = record(control, digest + "/activation.json")
    if (set(receipt) != {"format", "project", "inventory_sha256", "source_identity", "status", "marker"}
            or receipt["format"] != FORMAT or receipt["status"] != "activated"
            or receipt["inventory_sha256"] != digest or receipt["marker"] != marker
            or receipt["source_identity"] != marker["source_identity"]
            or not isinstance(receipt["project"], str)
            or not os.path.samefile(receipt["project"], root.parent)):
        raise ValueError("Migration publication is incomplete or belongs to another project")
    if record(root, SERVICE + "/project.json") != identity:
        raise ValueError("Migrated project identity changed")
    inventory_digest = marker["inventory_document_sha256"]
    if not isinstance(inventory_digest, str) or re.fullmatch("[a-f0-9]{64}", inventory_digest) is None:
        raise ValueError("Invalid migration inventory fingerprint")
    inventory_raw = read_bytes(control / digest, "inventory.json", INVENTORY_RECORD_LIMIT)
    if hashlib.sha256(inventory_raw).hexdigest() != inventory_digest:
        raise ValueError("Migration inventory changed or disappeared")
    inventory = _parse_record(inventory_raw)
    if (inventory.get("format") != "sico.state.migration.inventory.v1"
            or inventory.get("inventory_sha256") != digest
            or not isinstance(inventory.get("source"), str)
            or not isinstance(inventory.get("target"), str)):
        raise ValueError("Migration inventory is not bound to this project")
    try:
        if (not os.path.samefile(inventory["source"], root.parent / ".cad")
                or not os.path.samefile(inventory["target"], root)):
            raise ValueError("Migration inventory is not bound to this project")
    except OSError as exc:
        raise ValueError("Migration inventory is not bound to this project") from exc
    agent = directory(root / "ai/agent")
    directory(root / SERVICE)
    lock = (root / SERVICE / "owner.lock").lstat()
    read_bytes(root, SERVICE + "/owner.lock", 0)
    if (identity.get("directory_inode") != agent.st_ino or identity.get("lock_inode") != lock.st_ino
            or same_host and identity.get("directory_device") != agent.st_dev):
        raise ValueError("Migrated project directory or lock binding changed")
    history = marker["history_sha256"]
    if history is not None:
        if not isinstance(history, str) or re.fullmatch("[a-f0-9]{64}", history) is None:
            raise ValueError("Invalid migrated history fingerprint")
        if hashlib.sha256(read_bytes(root, "ai/agent/history-migration.json", 32 * 1024 * 1024)).hexdigest() != history:
            raise ValueError("Migrated history policy changed or disappeared")
    elif exists(root / "ai/agent/history-migration.json"):
        raise ValueError("Unexpected migrated history policy")
    return marker, inventory


def admission(root):
    """Return the completed marker or None for a project that has never migrated."""
    result = admission_payload(root)
    return None if result is None else result[0]
