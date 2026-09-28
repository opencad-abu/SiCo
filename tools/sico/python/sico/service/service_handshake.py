"""Version/capability negotiation and immutable per-connection wire identity."""

import re
import uuid
from dataclasses import dataclass

from ..transport.framing import ProtocolError
from .service_protocol import CAPABILITIES, FRAME_LIMIT, WIRE_PROTOCOL, exact_fields, require_id

HELLO_FIELDS = {"protocol", "kind", "project_id", "service_id", "client_id", "nonce",
                "capabilities", "required", "purpose"}
BASE_FIELDS = {"protocol", "kind", "project_id", "service_id", "client_id", "nonce"}


class ServiceIncompatible(ProtocolError):
    pass


class ServiceStopping(ConnectionError):
    pass


@dataclass(frozen=True)
class ConnectionIdentity:
    project_id: str
    service_id: str
    client_id: str
    connection_id: str
    capabilities: tuple


def _capabilities(value):
    if (not isinstance(value, list) or len(value) > 16
            or any(not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9_.]{0,63}", item)
                   for item in value) or len(set(value)) != len(value)):
        raise ProtocolError("Invalid service capability list")
    return tuple(value)


def hello_message(descriptor, *, purpose="attach", client_id=None,
                  capabilities=CAPABILITIES, required=CAPABILITIES):
    row = dict(protocol=WIRE_PROTOCOL, kind="hello", project_id=descriptor.project_id,
               service_id=descriptor.service_id,
               client_id=uuid.uuid4().hex if client_id is None else client_id,
               nonce=uuid.uuid4().hex, purpose=purpose,
               capabilities=list(capabilities), required=list(required))
    validate_hello(row, descriptor)
    return row


def validate_hello(row, descriptor):
    exact_fields(row, HELLO_FIELDS)
    if (row["kind"] != "hello" or row["purpose"] not in ("attach", "inspect")
            or not isinstance(row["protocol"], str) or not 0 < len(row["protocol"]) <= 64
            or row["project_id"] != descriptor.project_id
            or row["service_id"] != descriptor.service_id):
        raise ProtocolError("Invalid service handshake identity")
    for field in ("client_id", "nonce"):
        require_id(row[field])
    offered, required = _capabilities(row["capabilities"]), _capabilities(row["required"])
    if not set(required).issubset(offered):
        raise ProtocolError("Required capabilities were not offered")


def _base(request, kind):
    return dict({key: request[key] for key in BASE_FIELDS}, protocol=WIRE_PROTOCOL, kind=kind)


def negotiate(request, descriptor, lifecycle):
    validate_hello(request, descriptor)
    code = None
    if request["protocol"] != WIRE_PROTOCOL:
        code = "protocol_mismatch"
    elif not set(request["required"]).issubset(CAPABILITIES):
        code = "missing_capability"
    elif lifecycle.stopping or (request["purpose"] == "attach" and not lifecycle.attach()):
        code = "service_stopping"
    if code:
        return dict(_base(request, "rejected"), code=code), None
    selected = tuple(cap for cap in CAPABILITIES if cap in request["capabilities"])
    identity = ConnectionIdentity(descriptor.project_id, descriptor.service_id,
                                  request["client_id"], uuid.uuid4().hex, selected)
    return dict(_base(request, "ready"), connection_id=identity.connection_id,
                capabilities=list(selected), frame_limit=FRAME_LIMIT), identity


def accept_ready(reply, request):
    if not isinstance(reply, dict):
        raise ProtocolError("Invalid service handshake response")
    kind = reply.get("kind")
    if kind == "rejected":
        exact_fields(reply, BASE_FIELDS | {"code"})
        if (any(reply[key] != value for key, value in _base(request, kind).items())
                or reply["code"] not in (
                    "protocol_mismatch", "missing_capability", "service_stopping")):
            raise ProtocolError("Invalid service handshake rejection")
        if reply["code"] == "service_stopping":
            raise ServiceStopping("Service is stopping; rediscover after ownership release")
        raise ServiceIncompatible("Incompatible service handshake: " + reply["code"])
    exact_fields(reply, BASE_FIELDS | {"connection_id", "capabilities", "frame_limit"})
    if (any(reply[key] != value for key, value in _base(request, "ready").items())
            or type(reply["frame_limit"]) is not int or reply["frame_limit"] != FRAME_LIMIT):
        raise ProtocolError("Invalid service ready response")
    require_id(reply["connection_id"])
    selected = _capabilities(reply["capabilities"])
    if (not set(request["required"]).issubset(selected)
            or not set(selected).issubset(request["capabilities"])):
        raise ProtocolError("Invalid negotiated service capabilities")
    return ConnectionIdentity(reply["project_id"], reply["service_id"], reply["client_id"],
                              reply["connection_id"], selected)
