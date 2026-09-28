"""Durable resource-release receipt, written only after the session writer closes."""

from ..core.contracts import identifier
from ..transport.framing import ProtocolError
from .project_files import RegistryError, read_record, write_record

NAME = "end-release.json"


def save_release(directory, address, operation_id, sequence):
    write_record(_release_path(directory, address.session.runtime_id, operation_id),
        dict(version=1, address=address.record(),
        operation_id=operation_id, sequence=sequence))


def _release_path(directory, runtime_id, operation_id):
    return directory / "end-releases" / (
        identifier(runtime_id) + "." + identifier(operation_id) + ".json")


def read_release(directory, event):
    """Read the addressed receipt, retaining single-runtime evidence as read-only input."""
    payload = event["payload"]
    try:
        path = _release_path(directory, payload["address"]["runtime_id"], payload["operation_id"])
        try:
            return read_record(path)
        except FileNotFoundError:
            row = read_record(directory / NAME)
            if (isinstance(row, dict) and (row.get("operation_id") != payload["operation_id"]
                    or isinstance(row.get("address"), dict) and
                    row["address"].get("runtime_id") != payload["address"]["runtime_id"])):
                return None
            return row
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RegistryError) as exc:
        raise ProtocolError("Invalid resource release evidence") from exc


def validate_release(row, event):
    """Only the matching observed end can establish durable resource release."""
    if row is None or event["kind"] != "session.end_observed":
        return False
    payload = event["payload"]
    expected = dict(version=1, address=payload["address"],
                    operation_id=payload["operation_id"], sequence=event["sequence"])
    if (not isinstance(row, dict) or row != expected
            or type(row.get("sequence")) is not int or type(row.get("version")) is not int):
        raise ProtocolError("Resource release evidence belongs to another end intent")
    return event["kind"] == "session.end_observed" and payload.get("state") == "drained"


def released(directory, event):
    if event["kind"] != "session.end_observed":
        return False
    return validate_release(read_release(directory, event), event)
