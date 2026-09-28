"""Small host references and bounded memory-only Ask payloads."""

import hashlib
import json
import re

from ..transport.framing import ProtocolError, strict_json
from ..transport.targets import submission
from .service_protocol import exact_fields
from .service_values import name

HOST_FIELDS = {"instance_id", "generation", "bridge_id", "router_id"}
PAYLOAD_LIMIT = 512 * 1024


def host_identity(descriptor):
    result = {key: descriptor[key] for key in HOST_FIELDS}
    for value in result.values():
        name(value)
    return result


def digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def validate_reference(params):
    exact_fields(params, {"bridge", "payload_id", "digest"})
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


def capture_payload(value):
    """No file spooling or credentials in discovery; maximum one bounded bridge frame."""
    try:
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("ascii")
        if len(raw) > PAYLOAD_LIMIT:
            raise ProtocolError("Host payload exceeds limit")
        row = strict_json(raw)
        exact_fields(row, {"message", "provider_config", "environment"})
        submission(row["message"])
        if row["provider_config"] is not None and not isinstance(row["provider_config"], dict):
            raise ProtocolError("Invalid host configuration")
        env = row["environment"]
        if not isinstance(env, dict) or len(env) > 2048 or any(
                not isinstance(v, str) or not k or "=" in k or "\0" in k or "\0" in v
                for k, v in env.items()):
            raise ProtocolError("Invalid host environment")
        return row
    except ProtocolError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        raise ProtocolError("Invalid host payload") from exc
