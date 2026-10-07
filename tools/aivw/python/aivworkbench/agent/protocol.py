"""Compatibility exports for the AIVW agent protocol.

New consumers import the action, messages, errors, constants or codec owner.
Remove this facade after supported provider/runtime clients migrate to those
owners in the next incompatible protocol API release. It stores no state.
"""

from .protocol_constants import (
    ACTION_KINDS,
    MAX_ID_LENGTH as MAX_ID_LENGTH,
    MAX_JSONL_RECORDS,
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    ActionKind,
    ErrorCode,
    EventType,
)
from .protocol_errors import AgentError, ProtocolError
from .protocol_action import Action
from .protocol_envelope import Envelope, make_request, make_response
from .protocol_event import Event, make_event
from .protocol_codec import decode_json, decode_jsonl, encode_json, encode_jsonl

__all__ = [
    "ACTION_KINDS",
    "Action",
    "ActionKind",
    "AgentError",
    "Envelope",
    "ErrorCode",
    "Event",
    "EventType",
    "MAX_MESSAGE_BYTES",
    "MAX_JSONL_RECORDS",
    "PROTOCOL_VERSION",
    "ProtocolError",
    "decode_json",
    "decode_jsonl",
    "encode_json",
    "encode_jsonl",
    "make_event",
    "make_request",
    "make_response",
]
