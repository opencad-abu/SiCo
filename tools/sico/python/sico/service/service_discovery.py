"""Read-only project service discovery; candidates still require an authenticated handshake."""

import os
import socket
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from sicomigration import local_host_id

from ..storage.project_files import RegistryError, read_record, registry_directory
from ..storage.project_lock import ProjectLock
from .background_watchdog import boot_id, process_identity
from .project_identity import read_identity, valid_id

PROTOCOL = "sico_project_service.v1"


@dataclass(frozen=True)
class HostIdentity:
    host_id: str
    hostname: str
    boot_id: str

    def __post_init__(self):
        if (not valid_id(self.host_id) or not isinstance(self.hostname, str)
                or not 0 < len(self.hostname) <= 255 or any(c.isspace() for c in self.hostname)
                or not isinstance(self.boot_id, str)):
            raise RegistryError("invalid_host", "Invalid service host identity")
        try:
            valid_boot = str(uuid.UUID(self.boot_id)) == self.boot_id
        except ValueError:
            valid_boot = False
        if not valid_boot:
            raise RegistryError("invalid_host", "Invalid service boot identity")


def local_host():
    hostname = socket.gethostname()
    try:
        key = local_host_id()
    except ValueError as exc:
        raise RegistryError("host_unavailable", "Stable machine identity is unavailable") from exc
    return HostIdentity(key, hostname, boot_id())


@dataclass(frozen=True)
class ServiceDescriptor:
    project_id: str
    service_id: str
    host: HostIdentity
    pid: int
    process_start: str
    endpoint: str

    def __post_init__(self):
        if (not valid_id(self.project_id) or not valid_id(self.service_id)
                or not isinstance(self.host, HostIdentity) or type(self.pid) is not int
                or self.pid <= 0 or not isinstance(self.process_start, str)
                or not self.process_start.isascii() or not self.process_start.isdigit()
                or len(self.process_start) > 32):
            raise RegistryError("invalid_discovery", "Invalid service process identity")
        if (not isinstance(self.endpoint, str) or not self.endpoint.startswith("/")
                or len(os.fsencode(self.endpoint)) > 107 or "\0" in self.endpoint
                or ".." in Path(self.endpoint).parts):
            raise RegistryError("invalid_discovery", "Invalid local service endpoint")

    def record(self):
        return dict(protocol=PROTOCOL, **asdict(self))


@dataclass(frozen=True)
class Discovery:
    state: str
    project_id: Optional[str] = None
    service: Optional[ServiceDescriptor] = None


def read_descriptor(directory):
    row = read_record(directory / "discovery.json")
    if (row.pop("protocol", None) != PROTOCOL
            or set(row) != {"project_id", "service_id", "host", "pid", "process_start", "endpoint"}
            or not isinstance(row["host"], dict)
            or set(row["host"]) != {"host_id", "hostname", "boot_id"}):
        raise RegistryError("invalid_discovery", "Invalid service discovery schema")
    row["host"] = HostIdentity(**row["host"])
    return ServiceDescriptor(**row)


def occupied_discovery(directory, host):
    """Called only after observing a busy lock; the result is a hint, not ownership."""
    try:
        identity = read_identity(directory)
    except FileNotFoundError:
        if (directory / "discovery.json").exists():
            raise RegistryError("missing_identity", "Existing service lost its project identity")
        return Discovery("starting")
    identity.verify(directory, host.host_id)
    try:
        descriptor = read_descriptor(directory)
    except FileNotFoundError:
        return Discovery("starting", identity.project_id)
    if descriptor.project_id != identity.project_id:
        raise RegistryError("identity_mismatch", "Service belongs to a different project")
    if descriptor.host.host_id != host.host_id:
        return Discovery("other_host", identity.project_id, descriptor)
    if descriptor.host.boot_id != host.boot_id:
        return Discovery("unverified", identity.project_id)
    try:
        process = process_identity(descriptor.pid)
    except (OSError, ValueError, IndexError):
        return Discovery("unverified", identity.project_id)
    if process["start"] != descriptor.process_start or process["state"] in {"Z", "X"}:
        return Discovery("unverified", identity.project_id)
    return Discovery("local_candidate", identity.project_id, descriptor)


def discover_project(project):
    """Backend-only filesystem operation. Never creates, repairs or takes ownership."""
    directory = registry_directory(project)
    if directory is None:
        return Discovery("absent")
    host = local_host()
    try:
        lock = ProjectLock(directory)
    except FileNotFoundError:
        if any((directory / name).exists() for name in ("project.json", "discovery.json")):
            raise RegistryError("missing_lock", "Existing registry lost its ownership lock")
        return Discovery("absent")
    except BlockingIOError:
        return occupied_discovery(directory, host)
    with lock:
        try:
            identity = read_identity(directory)
        except FileNotFoundError:
            if (directory / "discovery.json").exists():
                raise RegistryError("missing_identity",
                                    "Existing service lost its project identity")
            return Discovery("absent")
        identity.verify(directory, host.host_id)
        # A free lock invalidates any discovery file, even a live or reused PID.
        return Discovery("inactive", identity.project_id)
