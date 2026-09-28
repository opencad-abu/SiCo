"""Bounded frontend transfer values; never serialize arbitrary Python resources."""

import base64
import hashlib
import json
import math
import re

from ..core.contracts import CONTEXT_CONTRACT, BoundContext
from ..transport.framing import ProtocolError, strict_json
from .published import freeze
from .service_protocol import exact_fields
from .service_values import MAX_SEQUENCE, integer, json_view

CHUNK_BYTES = 1800
PAYLOAD_BYTES = 4 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decode_chunk(value):
    try:
        if not isinstance(value, str) or len(value) > CHUNK_BYTES * 4 // 3:
            raise ValueError()
        data = base64.b64decode(value, validate=True)
        if len(data) > CHUNK_BYTES:
            raise ValueError()
        return data
    except (ValueError, TypeError) as exc:
        raise ProtocolError("Invalid frontend chunk") from exc


def encode_chunk(data):
    return base64.b64encode(data).decode("ascii")


def transfer_params(params):
    params = json_view(params)
    if not isinstance(params, dict):
        raise ProtocolError("Expected transfer parameters")
    step = params.get("step")
    fields = {"put": {"step", "offset", "data"}, "run": {"step", "size", "digest"},
              "read": {"step", "offset"}, "reset": {"step"}}
    if not isinstance(step, str) or step not in fields:
        raise ProtocolError("Unknown frontend transfer step")
    exact_fields(params, fields[step])
    if step in {"put", "read"}:
        integer(params["offset"])
    if step == "put":
        decode_chunk(params["data"])
    if step == "run":
        integer(params["size"], 1)
        if (params["size"] > PAYLOAD_BYTES or not isinstance(params["digest"], str)
                or re.fullmatch(r"[a-f0-9]{64}", params["digest"]) is None):
            raise ProtocolError("Invalid frontend payload manifest")
    return params


def _json(value, budget, depth=0):
    budget[0] -= 1
    if depth > 32 or budget[0] < 0 or budget[1] < 0:
        raise ProtocolError("Frontend nesting or content budget exceeded")
    if isinstance(value, BoundContext):
        # Validate the snapshot before record()/JSON copying can amplify it.
        row = dict(contract=CONTEXT_CONTRACT, instance_id=value.instance_id,
                   generation=value.generation, target_id=value.target_id,
                   snapshot=value.snapshot)
        return {"$context": _json(row, budget, depth + 1)}
    if isinstance(value, dict):
        if len(value) > budget[0]:
            raise ProtocolError("Frontend item budget exceeded")
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or key == "$context" or len(key) > 256:
                raise ProtocolError("Invalid frontend field")
            budget[1] -= len(key) + 4
            result[key] = _json(item, budget, depth + 1)
        return result
    if isinstance(value, (tuple, list)):
        if len(value) > budget[0]:
            raise ProtocolError("Frontend item budget exceeded")
        return [_json(item, budget, depth + 1) for item in value]
    if type(value) is str:
        budget[1] -= len(value) + 3
        if budget[1] < 0:
            raise ProtocolError("Frontend text budget exceeded")
    if ((type(value) is int and abs(value) > MAX_SEQUENCE)
            or (type(value) is float and not math.isfinite(value))):
        raise ProtocolError("Invalid frontend number")
    if value is None or type(value) in (bool, int, float, str):
        return value
    raise ProtocolError("Only declared frontend values may cross the boundary")


def encode_payload(value, *, limit=PAYLOAD_BYTES):
    try:
        data = json.dumps(_json(value, [200000, limit]), ensure_ascii=True, allow_nan=False,
                          separators=(",", ":")).encode("ascii")
    except (ValueError, TypeError, RecursionError) as exc:
        raise ProtocolError("Frontend payload is not bounded JSON") from exc
    if len(data) > limit:
        raise ProtocolError("Frontend publication exceeds its budget")
    return data


def decode_payload(data, *, limit=PAYLOAD_BYTES):
    remaining = [200000]
    def restore(value, depth=0):
        remaining[0] -= 1
        if depth > 32 or remaining[0] < 0:
            raise ProtocolError("Frontend nesting limit exceeded")
        if isinstance(value, dict):
            if "$context" in value:
                exact_fields(value, {"$context"})
                record = value["$context"]
                exact_fields(record, {"contract", "instance_id", "generation", "target_id",
                                      "snapshot"})
                try:
                    bound = BoundContext.from_record(restore(record, depth + 1))
                except (ValueError, TypeError, KeyError) as exc:
                    raise ProtocolError("Invalid frontend context") from exc
                object.__setattr__(bound, "snapshot", freeze(bound.snapshot))
                return bound
            return freeze({key: restore(item, depth + 1) for key, item in value.items()})
        if isinstance(value, list):
            return freeze([restore(item, depth + 1) for item in value])
        if type(value) is int and abs(value) > MAX_SEQUENCE:
            raise ProtocolError("Frontend number exceeds its budget")
        return value
    if len(data) > limit:
        raise ProtocolError("Frontend payload limit exceeded")
    return restore(strict_json(bytes(data)))


class TransferResult:
    def __init__(self, row):
        if not isinstance(row, dict) or not isinstance(row.get("state"), str):
            raise ProtocolError("Invalid frontend transfer response")
        self.value = json_view(row)

    def record(self):
        return self.value
