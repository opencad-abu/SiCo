"""Bounded JSON values for wire DTOs, before immutable background publication."""

import math

from ..core.contracts import identifier
from ..transport.framing import ProtocolError, encode
from .published import FrozenDict, FrozenList, freeze

MAX_SEQUENCE = 2**63 - 1
DTO_BYTES = 3072  # Leave space for the authenticated envelope inside a 4 KiB frame.


def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_SEQUENCE:
        raise ProtocolError("Invalid DTO sequence or version")
    return value


def name(value):
    try:
        return identifier(value)
    except ValueError as exc:
        raise ProtocolError("Invalid DTO identifier") from exc


def json_view(value):
    """Reject handles, non-finite numbers and excessive work; never stringify objects."""
    remaining = [2048]

    def visit(item, depth):
        remaining[0] -= 1
        if depth > 16 or remaining[0] < 0:
            raise ProtocolError("DTO nesting or item budget exceeded")
        if item is None or type(item) is bool:
            return
        if type(item) is int:
            if abs(item) > MAX_SEQUENCE:
                raise ProtocolError("DTO integer exceeds its budget")
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is str:
            if len(item) > DTO_BYTES:
                raise ProtocolError("DTO text exceeds its budget")
            return
        if type(item) in (dict, FrozenDict):
            if len(item) > 2048:
                raise ProtocolError("DTO item budget exceeded")
            for key, child in item.items():
                if type(key) is not str or len(key) > 96:
                    raise ProtocolError("Invalid DTO key")
                visit(child, depth + 1)
            return
        if type(item) in (list, tuple, FrozenList):
            if len(item) > 2048:
                raise ProtocolError("DTO item budget exceeded")
            for child in item:
                visit(child, depth + 1)
            return
        raise ProtocolError("Only JSON values may cross the service boundary")

    visit(value, 0)
    try:
        encode({"value": value}, max_frame=DTO_BYTES)
    except UnicodeError as exc:
        raise ProtocolError("Invalid DTO Unicode text") from exc
    return freeze(value)
