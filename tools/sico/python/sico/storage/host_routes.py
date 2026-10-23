"""Durable host input destination, separate from the inbox acceptance fact."""

import os
import math
import re

from ..core.ask_queue import ask_sequence

from ..core.contracts import identifier
from ..core.host_contracts import digest, host_identity, session_address_record
from cadai.json_values import ProtocolError
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
        if (set(row) - {"ask_sequence", "received_at", "rejection"} != set(expected) | {"service_id", "session_id", "payload_digest", "address"}
                or any(row[key] != value for key, value in expected.items())):
            raise ProtocolError("Invalid host route identity")
        try:
            if "ask_sequence" in row:
                ask_sequence(row["ask_sequence"])
            if "received_at" in row and (type(row["received_at"]) not in (int, float)
                                         or not math.isfinite(row["received_at"])):
                raise ValueError("Invalid receipt time")
            if (not isinstance(row["payload_digest"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", row["payload_digest"])):
                raise ValueError("Invalid host digest")
            if "rejection" in row:
                receipt = row["rejection"]
                if (not isinstance(receipt, dict) or receipt.get("outcome") != "rejected"
                        or receipt.get("input_id") != input_id
                        or receipt.get("address") != row["address"]):
                    raise ValueError("Invalid rejection receipt")
            identifier(row["service_id"])
            identifier(row["session_id"])
        except (ValueError, TypeError) as exc:
            raise ProtocolError("Invalid host route identity") from exc
        if row["address"] is not None:
            try:
                session_address_record(
                    row["address"], project_id=self.project_id,
                    service_id=row["service_id"], session_id=row["session_id"],
                    require_runtime=True,
                )
            except (ValueError, TypeError, KeyError) as exc:
                raise ProtocolError("Invalid host route destination") from exc
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
