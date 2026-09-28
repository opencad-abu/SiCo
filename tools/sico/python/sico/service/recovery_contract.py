"""Bounded recovery inspection and explicit intent, without secrets in observations."""

import re

from ..core.contracts import BoundContext
from ..transport.framing import ProtocolError
from .service_protocol import exact_fields
from .service_session_dto import SessionAddress
from .service_values import name


def version(value):
    if not isinstance(value, str) or re.fullmatch("[a-f0-9]{64}", value) is None:
        raise ProtocolError("Invalid recovery evidence revision")


def recovery_request(params):
    exact_fields(params, {"session_id", "version", "credentials"})
    name(params["session_id"])
    version(params["version"])
    values = params["credentials"]
    if (not isinstance(values, dict) or len(values) > 1
            or any(not isinstance(k, str) or not k.isidentifier() or not isinstance(v, str)
                   or len(v) > 8192 or "\0" in v for k, v in values.items())):
        raise ProtocolError("Invalid recovery credential")


def recovery_operation(operation):
    exact_fields(operation, {"operation_id", "service_id", "runtime_id", "version"})
    for key in ("operation_id", "service_id", "runtime_id"):
        name(operation[key])
    version(operation["version"])


def recovery_view(row, descriptor, session_id, operation_id=None):
    exact_fields(row, {"project_id", "service_id", "session_id", "version", "state", "credential",
                       "context", "task_id", "task_status", "inputs", "session", "operation"})
    if (row["project_id"] != descriptor.project_id or row["service_id"] != descriptor.service_id
            or row["session_id"] != session_id or not isinstance(row["context"], BoundContext)
            or row["state"] not in {"live", "ended", "legacy_readonly", "archived_readonly",
                                    "waiting_credentials", "ready"}
            or row["task_status"] not in {"", "completed", "failed", "cancelled", "executing",
                                          "needs_reconcile"}):
        raise ProtocolError("Recovery observation belongs to another source")
    version(row["version"])
    if row["task_id"]:
        name(row["task_id"])
    if row["credential"] is not None and (not isinstance(row["credential"], str)
                                         or not row["credential"].isidentifier()):
        raise ProtocolError("Invalid credential reference")
    if not isinstance(row["inputs"], (list, tuple)) or len(row["inputs"]) > 200:
        raise ProtocolError("Recovery input budget exceeded")
    seen = set()
    for item in row["inputs"]:
        exact_fields(item, {"input_id", "status", "task_id", "address"})
        name(item["input_id"])
        if item["input_id"] in seen or item["status"] not in {
                "queued", "executing", "waiting_user", "needs_reconcile"}:
            raise ProtocolError("Invalid recovery input state")
        seen.add(item["input_id"])
        if item["task_id"] is not None:
            name(item["task_id"])
        if item["address"] is not None:
            address = SessionAddress.from_record(item["address"])
            if (address.project_id != descriptor.project_id
                    or address.session.session_id != session_id):
                raise ProtocolError("Recovery input belongs to another session")
    if (row["state"] == "live") != (row["session"] is not None):
        raise ProtocolError("Invalid recovery runtime observation")
    if row["session"] is not None:
        address = SessionAddress.from_record(row["session"]["address"])
        if (address.project_id, address.service_id, address.session.session_id) != (
                descriptor.project_id, descriptor.service_id, session_id):
            raise ProtocolError("Recovered runtime belongs to another session")
    operation = row["operation"]
    if operation is not None:
        recovery_operation(operation)
        if operation["operation_id"] != operation_id:
            raise ProtocolError("Recovery result belongs to another operation")
    return row


def validate_review(row, session_id):
    exact_fields(row, {"session_id", "version", "requires_review", "task_status", "input_count"}
                 | ({"damaged"} if "damaged" in row else set()))
    if "damaged" in row and row["damaged"] is not True:
        raise ProtocolError("Invalid incomplete-record review")
    version(row["version"])
    if (row["session_id"] != session_id or type(row["requires_review"]) is not bool
            or row["task_status"] not in {"", "completed", "failed", "cancelled", "executing",
                                          "needs_reconcile"}
            or type(row["input_count"]) is not int or not 0 <= row["input_count"] <= 200):
        raise ProtocolError("Invalid deletion review")
    return row
