"""Strict wire envelopes and control claims; authority is resolved by SessionControl."""

import uuid
from dataclasses import dataclass

from ..core.contracts import identifier
from ..transport.framing import ProtocolError
from .project_identity import valid_id
from .service_values import name

WIRE_PROTOCOL = "sico_agent_service.v1"
FRAME_LIMIT = 4096
METHODS = {"service.status": "service.status.v1", "service.stop": "service.stop.v1",
           "service.operation": "service.operation.v1",
           "session.open": "session.open.v1", "session.submit": "session.submit.v1",
           "session.input": "session.input.v1", "host.submit": "host.submit.v1",
           "session.control": "session.control.v1",
           "host.input": "host.input.v1",
           **{"frontend." + key: "frontend." + key + ".v1"
              for key in ("attach", "events", "query", "command")}}
CAPABILITIES = tuple(METHODS.values())
MAX_REQUEST_ID = 2**31 - 1
IDENTITY_FIELDS = {"protocol", "project_id", "service_id", "client_id", "connection_id"}
REQUEST_FIELDS = IDENTITY_FIELDS | {
    "kind", "request_id", "operation_id", "method", "control", "params"
}


def exact_fields(row, fields):
    if not isinstance(row, dict) or set(row) != fields:
        raise ProtocolError("Invalid service message fields")


def require_id(value):
    if not valid_id(value):
        raise ProtocolError("Invalid service identity")
    return value


@dataclass(frozen=True)
class ControlClaim:
    """Untrusted proof fields; SessionControl verifies the live connection and lease."""

    client_id: str
    session_id: str
    runtime_id: str
    generation: int
    lease_id: str


def control_claim(row, client_id):
    exact_fields(row, {"client_id", "session_id", "runtime_id", "generation", "lease_id"})
    for key in ("client_id", "runtime_id", "lease_id"):
        require_id(row[key])
    try:
        identifier(row["session_id"])
    except ValueError as exc:
        raise ProtocolError("Invalid control session identity") from exc
    if (row["client_id"] != client_id or type(row["generation"]) is not int
            or not 0 < row["generation"] <= MAX_REQUEST_ID):
        raise ProtocolError("Invalid control claim binding")
    return ControlClaim(**row)


def envelope(binding):
    return dict(protocol=WIRE_PROTOCOL, project_id=binding.project_id,
                service_id=binding.service_id,
                client_id=binding.client_id, connection_id=binding.connection_id)


def validate_request(row, binding, next_request_id):
    exact_fields(row, REQUEST_FIELDS)
    if (row["kind"] != "request"
            or any(row[key] != value for key, value in envelope(binding).items())
            or type(row["request_id"]) is not int or row["request_id"] != next_request_id
            or not 0 < row["request_id"] <= MAX_REQUEST_ID):
        raise ProtocolError("Stale service request or connection identity")
    name(row["operation_id"])
    method = row["method"]
    if not isinstance(method, str) or method not in METHODS:
        raise ProtocolError("Unsupported service method")
    if METHODS[method] not in binding.capabilities:
        raise ProtocolError("Service capability was not negotiated")
    if row["control"] is not None:
        control_claim(row["control"], binding.client_id)
        if method not in {"session.submit", "frontend.command"}:
            raise ProtocolError("Session control is unavailable for this request")
    if method in ("service.status", "service.stop"):
        exact_fields(row["params"], set())
    elif method == "service.operation":
        exact_fields(row["params"], {"operation_id"})
        name(row["params"]["operation_id"])
        if row["params"]["operation_id"] != row["operation_id"]:
            raise ProtocolError("Operation lookup identity mismatch")
    elif method == "session.control":
        from .control_contract import control_params

        control_params(row["params"])
    elif method.startswith("frontend."):
        from .frontend_codec import transfer_params

        transfer_params(row["params"])
    else:
        from .service_business_dto import business_params

        business_params(method, row["params"])
        if (method in {"session.submit", "session.input"}
                and row["params"]["address"]["project_id"] != binding.project_id):
            raise ProtocolError("Session belongs to another project")


def request_message(binding, method, request_id, *, operation_id=None, control=None, params=None):
    row = dict(envelope(binding), kind="request", method=method,
               request_id=request_id,
               operation_id=uuid.uuid4().hex if operation_id is None else operation_id,
               control=control, params={} if params is None else params)
    validate_request(row, binding, request_id)
    return row


def result_message(request, result):
    return dict({key: request[key] for key in IDENTITY_FIELDS}, kind="result",
                request_id=request["request_id"], operation_id=request["operation_id"],
                method=request["method"], result=result)


def validate_result(row, request):
    exact_fields(row, IDENTITY_FIELDS | {"kind", "request_id", "operation_id", "method", "result"})
    expected = result_message(request, {})
    if (any(row[key] != value for key, value in expected.items() if key != "result")
            or type(row["request_id"]) is not int or row["operation_id"] != request["operation_id"]
            or not isinstance(row["result"], dict)):
        raise ProtocolError("Stale service response or connection identity")
    return row["result"]


def heartbeat_message(binding):
    return dict(envelope(binding), kind="heartbeat")


def validate_heartbeat(row, binding):
    exact_fields(row, IDENTITY_FIELDS | {"kind"})
    if row["kind"] != "heartbeat" or any(row[key] != value
                                           for key, value in envelope(binding).items()):
        raise ProtocolError("Invalid service heartbeat")


def heartbeat_ack(binding):
    return dict(envelope(binding), kind="heartbeat_ack")


def validate_heartbeat_ack(row, binding):
    exact_fields(row, IDENTITY_FIELDS | {"kind"})
    if row["kind"] != "heartbeat_ack" or any(row[key] != value
                                               for key, value in envelope(binding).items()):
        raise ProtocolError("Invalid service heartbeat acknowledgement")
