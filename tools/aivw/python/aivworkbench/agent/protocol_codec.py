"""Bounded JSON and JSONL framing for typed AIVW protocol values."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from .protocol_constants import MAX_JSONL_RECORDS, MAX_MESSAGE_BYTES, ErrorCode
from .protocol_errors import ProtocolError
from .protocol_action import Action
from .protocol_envelope import Envelope
from .protocol_event import Event
from .value_codec import ensure_json


def encode_json(value: Envelope | Event | Action | Mapping[str, Any]) -> str:
    if isinstance(value, (Envelope, Event, Action)):
        payload = value.to_dict()
    elif isinstance(value, Mapping):
        payload = dict(value)
        ensure_json(payload)
    else:
        raise ProtocolError(
            ErrorCode.INVALID_ENVELOPE, "value is not a protocol object"
        )
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ProtocolError(
            ErrorCode.CONTEXT_LIMIT_EXCEEDED, "protocol message exceeds hard cap"
        )
    return encoded


def encode_jsonl(value: Envelope | Event | Action | Mapping[str, Any]) -> str:
    return encode_json(value) + "\n"


def decode_json(
    raw: str | bytes, *, expected: str | None = None
) -> Envelope | Event | Action | dict[str, Any]:
    if isinstance(raw, bytes):
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ProtocolError(
                ErrorCode.CONTEXT_LIMIT_EXCEEDED, "protocol message exceeds hard cap"
            )
        raw = raw.decode("utf-8", errors="strict")
    if not isinstance(raw, str):
        raise ProtocolError(
            ErrorCode.INVALID_ENVELOPE, "protocol input is not valid text"
        )
    try:
        encoded_size = len(raw.encode("utf-8"))
    except UnicodeError as exc:
        raise ProtocolError(
            ErrorCode.INVALID_ENVELOPE, "protocol input is not valid UTF-8"
        ) from exc
    if encoded_size > MAX_MESSAGE_BYTES:
        raise ProtocolError(
            ErrorCode.INVALID_ENVELOPE, "protocol input is invalid or too large"
        )
    try:
        # JSON silently accepts duplicate object names by default.  A
        # duplicate can make two implementations authorize different values,
        # so reject it at the framing boundary instead of relying on the
        # last-wins behavior of ``dict``.
        value = json.loads(
            raw,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_object_names,
        )
    except (TypeError, ValueError, UnicodeError, _DuplicateObjectName) as exc:
        raise ProtocolError(
            ErrorCode.INVALID_ENVELOPE, "invalid JSON", {"detail": str(exc)}
        ) from exc
    if expected == "action":
        return Action.from_dict(value)
    if expected == "event":
        return Event.from_dict(value)
    if expected == "envelope":
        return Envelope.from_dict(value)
    if isinstance(value, Mapping):
        # Best-effort typed decoding.  Explicit ``expected`` remains the
        # preferred path because envelopes and events have overlapping fields.
        if "event_type" in value:
            return Event.from_dict(value)
        if "message_type" in value:
            return Envelope.from_dict(value)
        if "action_id" in value and "kind" in value:
            return Action.from_dict(value)
        return dict(value)
    raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "JSON root must be an object")


def decode_jsonl(
    lines: Iterable[str | bytes], *, expected: str | None = None
) -> list[Any]:
    decoded: list[Any] = []
    total_bytes = 0
    for index, line in enumerate(lines):
        if index >= MAX_JSONL_RECORDS:
            raise ProtocolError(
                ErrorCode.CONTEXT_LIMIT_EXCEEDED, "JSONL record count exceeds hard cap"
            )
        if not isinstance(line, (str, bytes)):
            raise ProtocolError(
                ErrorCode.INVALID_ENVELOPE, "JSONL record must be text or bytes"
            )
        if isinstance(line, bytes):
            total_bytes += len(line)
        else:
            try:
                total_bytes += len(str(line).encode("utf-8"))
            except UnicodeError as exc:
                raise ProtocolError(
                    ErrorCode.INVALID_ENVELOPE, "JSONL record is not valid text"
                ) from exc
        if total_bytes > MAX_MESSAGE_BYTES:
            raise ProtocolError(
                ErrorCode.CONTEXT_LIMIT_EXCEEDED, "JSONL stream exceeds hard cap"
            )
        if isinstance(line, bytes):
            blank = not line.strip()
        else:
            blank = not str(line).strip()
        if blank:
            continue
        try:
            decoded.append(decode_json(line, expected=expected))
        except ProtocolError as exc:
            raise ProtocolError(
                exc.code,
                "invalid JSONL record %d: %s" % (index, exc.message),
                exc.details,
            ) from exc
    return decoded


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant: %s" % value)


class _DuplicateObjectName(ValueError):
    """Internal marker used to reject duplicate JSON object names."""


def _reject_duplicate_object_names(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateObjectName("duplicate JSON object field: %s" % key)
        result[key] = value
    return result
