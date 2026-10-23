"""Durable sequence ownership within the host route authority."""

import os

from ..core.ask_queue import ask_sequence
from ..core.contracts import identifier
from ..core.host_contracts import digest, host_identity
from cadai.json_values import ProtocolError
from .history import owned_directory
from .journal import private_dir, sync_directory
from .project_files import RegistryError, read_record, record_fd, registry_directory, write_record


def claim_sequence(project, host, sequence, input_id):
    ask_sequence(sequence)
    identifier(input_id)
    identity = host_identity(host)
    key = dict(instance_id=identity["instance_id"], generation=identity["generation"],
               ask_sequence=sequence)
    directory = registry_directory(project, create=True) / "host-sequences"
    path = directory / (digest(key) + ".json")
    expected = dict(contract="sico_host_sequence.v1", **key, input_id=input_id)
    try:
        owned_directory(directory)
        row = read_record(path)
    except FileNotFoundError:
        private_dir(directory)
        write_record(path, expected)
        sync_directory(directory.parent)
        return True
    except (OSError, RegistryError, ValueError) as exc:
        raise ProtocolError("Invalid host sequence storage") from exc
    if (set(row) != set(expected) or any(row[k] != v for k, v in expected.items()
                                       if k != "input_id")):
        raise ProtocolError("Invalid host sequence identity")
    try:
        identifier(row["input_id"])
    except ValueError as exc:
        raise ProtocolError("Invalid host sequence input") from exc
    try:
        fd = record_fd(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        sync_directory(directory)
    except (OSError, RegistryError, ValueError) as exc:
        raise ProtocolError("Unable to confirm host sequence storage") from exc
    return row["input_id"] == input_id
