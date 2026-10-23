"""Service host references with compatibility exports for bridge payload values."""

import re

from ..core.host_contracts import (
    HOST_FIELDS,
    digest as digest,
    host_identity as _host_identity,
)
from ..transport.framing import ProtocolError
# Compat: retire these exports once service/external consumers use the transport contract.
from ..transport.host_payload_contract import PAYLOAD_LIMIT as PAYLOAD_LIMIT, capture_payload as capture_payload
from .service_protocol import exact_fields
from .service_values import name


def host_identity(descriptor):
    try:
        return _host_identity(descriptor)
    except (ValueError, TypeError, KeyError) as exc:
        raise ProtocolError("Invalid host identity") from exc


def validate_reference(params):
    exact_fields(params, {"bridge", "payload_id", "digest"} |
                 ({"ask_sequence"} if "ask_sequence" in params else set()))
    if "ask_sequence" in params:
        from ..core.ask_queue import ask_sequence

        try:
            ask_sequence(params["ask_sequence"])
        except ValueError as exc:
            raise ProtocolError("Invalid host Ask sequence") from exc
    name(params["payload_id"])
    if not isinstance(params["bridge"], dict):
        raise ProtocolError("Missing host bridge descriptor")
    try:
        host_identity(params["bridge"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ProtocolError("Invalid host identity") from exc
    if not isinstance(params["digest"], str) or not re.fullmatch(r"[a-f0-9]{64}", params["digest"]):
        raise ProtocolError("Invalid host payload digest")


def validate_query(params):
    exact_fields(params, {"host"})
    exact_fields(params["host"], HOST_FIELDS)
    host_identity(params["host"])
