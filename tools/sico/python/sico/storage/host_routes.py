"""Durable host input destination, separate from the inbox acceptance fact."""

import os

from ..service.host_contract import digest, host_identity
from ..service.service_session_dto import SessionAddress
from ..transport.framing import ProtocolError
from .journal import private_dir, sync_directory
from .project_files import RegistryError, read_record, record_fd, registry_directory, write_record


class HostRoutes:
    def __init__(self, project, project_id):
        self.project, self.project_id = project, project_id

    def path(self, host, input_id):
        key = digest(dict(host=host_identity(host), input_id=input_id))
        directory = registry_directory(self.project) / "host-inputs"
        return directory / (key + ".json")

    def read(self, host, input_id):
        path = self.path(host, input_id)
        try:
            from .history import owned_directory
            owned_directory(path.parent)
            row = read_record(path)
        except FileNotFoundError:
            return None
        except (OSError, RegistryError, ValueError) as exc:
            # A route is service evidence.  Permission, ownership, link and
            # malformed-file failures must stay protocol failures rather than
            # escaping as an ordinary value error or storage exception.
            raise ProtocolError("Invalid host route storage") from exc
        expected = dict(contract="sico_host_route.v1", project_id=self.project_id,
                        host=host_identity(host), input_id=input_id)
        if (set(row) != set(expected) | {"service_id", "session_id", "payload_digest", "address"}
                or any(row[key] != value for key, value in expected.items())):
            raise ProtocolError("Invalid host route identity")
        from ..service.service_values import name
        name(row["service_id"])
        name(row["session_id"])
        if row["address"] is not None:
            address = SessionAddress.from_record(row["address"])
            if (address.project_id != self.project_id or address.service_id != row["service_id"]
                    or address.session.session_id != row["session_id"]
                    or address.session.runtime_id is None):
                raise ProtocolError("Invalid host route destination")
        # A prior failed directory sync is still uncertain until confirmed here.
        try:
            fd = record_fd(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            sync_directory(path.parent)
        except (OSError, RegistryError, ValueError) as exc:
            raise ProtocolError("Unable to confirm host route storage") from exc
        return row

    def write(self, host, input_id, row):
        path = self.path(host, input_id)
        private_dir(path.parent)
        write_record(path, row)
        sync_directory(path.parent.parent)
