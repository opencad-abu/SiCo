"""Project service ownership capability; no GUI, listener or session construction."""

import os
import stat
import uuid
from pathlib import Path

from ..storage.journal import sync_directory
from ..storage.project_files import RegistryError, registry_directory, write_record
from ..storage.project_lock import ProjectLock
from .background_watchdog import process_identity
from .project_identity import create_identity, read_identity
from .service_discovery import ServiceDescriptor, local_host, occupied_discovery
from .service_retirement import retire_endpoint
from .service_runtime import validate_endpoint


class ProjectBusy(RuntimeError):
    def __init__(self, discovery):
        self.discovery = discovery
        message = "Project service ownership is held"
        if discovery.state == "other_host":
            message += " on another host: " + discovery.service.host.hostname
        super().__init__(message)


class ProjectServiceLease:
    """One backend process owns this capability until close; never expose it to Qt."""

    def __init__(self, directory, lock, identity, host):
        self._directory, self._lock = directory, lock
        self._identity, self._host = identity, host
        self._service_id = uuid.uuid4().hex
        self._pid = os.getpid()
        self._process_start = process_identity(self._pid)["start"]

    @classmethod
    def acquire(cls, project):
        host = local_host()
        directory = registry_directory(project, create=True)
        try:
            lock = ProjectLock(directory, create=True)
        except BlockingIOError:
            raise ProjectBusy(occupied_discovery(directory, host)) from None
        try:
            try:
                identity = read_identity(directory)
            except FileNotFoundError:
                if (directory / "discovery.json").exists():
                    raise RegistryError("missing_identity",
                                        "Existing service lost its project identity")
                identity = create_identity(directory, host.host_id)
            identity.verify(directory, host.host_id)
            lock.check()
            retire_endpoint(directory, identity.project_id, host)
            # Retire the previous endpoint before the new service starts listening.
            (directory / "discovery.json").unlink(missing_ok=True)
            sync_directory(directory)
            return cls(directory, lock, identity, host)
        except BaseException:
            lock.close()
            raise

    @property
    def project_id(self):
        return self._identity.project_id

    @property
    def service_id(self):
        return self._service_id

    def publish(self, endpoint):
        """Publish only an already bound private Unix socket; readiness needs a handshake."""
        self._lock.check()
        self._identity.verify(self._directory, self._host.host_id)
        endpoint = Path(endpoint)
        descriptor = ServiceDescriptor(self.project_id, self.service_id, self._host,
                                       self._pid, self._process_start, str(endpoint))
        parent, info = endpoint.parent.lstat(), endpoint.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                or parent.st_mode & 0o077 or not stat.S_ISSOCK(info.st_mode)
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise RegistryError("unsafe_endpoint", "Expected a private owned local Unix socket")
        write_record(self._directory / "discovery.json", descriptor.record())
        self._lock.check()
        return descriptor

    def mark_runtime(self, endpoint):
        """Write generation evidence before publication so crash cleanup can fail closed."""
        self._lock.check()
        validate_endpoint(endpoint)
        descriptor = ServiceDescriptor(self.project_id, self.service_id, self._host,
                                       self._pid, self._process_start, str(endpoint))
        write_record(Path(endpoint).parent / "owner.json", descriptor.record())

    def close(self):
        # Leave discovery stale; next acquisition retires it under the same fixed lock.
        self._lock.close()

    def __enter__(self):
        self._lock.check()
        return self

    def __exit__(self, *_args):
        self.close()


def initialize_project_copy(project, *, expected_project_id):
    """Explicitly assign a copied data directory a new ID; valid originals cannot reset."""
    directory = registry_directory(project)
    if directory is None:
        raise RegistryError("missing_identity", "No copied project identity exists")
    host = local_host()
    with ProjectLock(directory):
        previous = read_identity(directory)
        if previous.project_id != expected_project_id or previous.matches(directory, host.host_id):
            raise RegistryError("not_a_copy",
                                "Expected a copied project with the supplied identity")
        (directory / "discovery.json").unlink(missing_ok=True)
        sync_directory(directory)
        return create_identity(directory, host.host_id).project_id
