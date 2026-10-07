"""Strict JSON values shared by AI tools and the SiCo transport."""

from __future__ import annotations

import json
import math
from typing import Any


class ProtocolError(ValueError):
    pass


def strict_json(raw: bytes | str) -> dict:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ProtocolError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ProtocolError("Non-finite JSON number")

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ProtocolError("Non-finite JSON number")
        return result

    try:
        source = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        value = json.loads(source, object_pairs_hook=pairs, parse_constant=constant,
                           parse_float=finite_float)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("Invalid JSON payload") from exc
    if not isinstance(value, dict):
        raise ProtocolError("Message must be an object")
    return value


def json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


