"""Bounded JSONL framing shared by the controller and MCP helper."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

MAX_LINE_BYTES = 1_048_576
_REQUEST_ID_PATTERN = re.compile(r"[0-9a-f][0-9a-f-]{15,63}\Z")


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str, request_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.request_id = request_id

    def as_error(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


class JsonLineDecoder:
    """Decode arbitrary byte fragments without splitting UTF-8 characters."""

    def __init__(self, max_line_bytes: int = MAX_LINE_BYTES) -> None:
        self.max_line_bytes = max_line_bytes
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        self._buffer.extend(data)
        messages: list[dict[str, Any]] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                if len(self._buffer) > self.max_line_bytes:
                    self._buffer.clear()
                    raise ProtocolError("line_too_long", "JSONL message exceeds the limit")
                return messages
            raw = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            if len(raw) > self.max_line_bytes:
                raise ProtocolError("line_too_long", "JSONL message exceeds the limit")
            if raw.strip():
                messages.append(decode_line(raw, max_line_bytes=self.max_line_bytes))

    def finish(self) -> None:
        if self._buffer.strip():
            self._buffer.clear()
            raise ProtocolError("truncated_message", "JSONL stream ended mid-message")
        self._buffer.clear()


def decode_line(raw: bytes | str, *, max_line_bytes: int = MAX_LINE_BYTES) -> dict[str, Any]:
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(encoded) > max_line_bytes:
        raise ProtocolError("line_too_long", "JSONL message exceeds the limit")
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid_encoding", "JSONL message must be UTF-8") from exc
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON object key: %s" % key)
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise ValueError("non-finite JSON number: %s" % value)

    try:
        value = json.loads(
            text,
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        column = getattr(exc, "colno", None)
        suffix = f" at column {column}" if column is not None else ""
        raise ProtocolError("invalid_json", f"invalid JSON{suffix}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ProtocolError("invalid_message", "JSONL message must be an object")
    if any(isinstance(item, float) and not math.isfinite(item) for item in _walk_json(value)):
        raise ProtocolError("invalid_json", "JSONL message contains a non-finite number")
    return dict(value)


def encode_line(value: Mapping[str, Any], *, max_line_bytes: int = MAX_LINE_BYTES) -> bytes:
    try:
        encoded = (
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolError("not_serializable", "message is not JSON serializable") from exc
    if len(encoded) > max_line_bytes:
        raise ProtocolError("line_too_long", "JSONL message exceeds the limit")
    return encoded


def _walk_json(value: Any):
    """Yield nested JSON values for a final non-finite-number guard."""
    yield value
    if isinstance(value, Mapping):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_json(child)


def require_request(value: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    request_id = value.get("id")
    method = value.get("method")
    params = value.get("params", {})
    if not isinstance(request_id, str) or not _REQUEST_ID_PATTERN.fullmatch(request_id):
        raise ProtocolError(
            "invalid_id",
            "request id must be 16 to 64 lowercase hexadecimal or hyphen characters",
        )
    if not isinstance(method, str) or not method:
        raise ProtocolError("invalid_method", "method must be a non-empty string", request_id)
    if not isinstance(params, Mapping):
        raise ProtocolError("invalid_params", "params must be an object", request_id)
    return request_id, method, dict(params)


def require_response(value: Mapping[str, Any]) -> tuple[str, bool, dict[str, Any]]:
    request_id = value.get("id")
    ok = value.get("ok")
    if not isinstance(request_id, str) or not request_id:
        raise ProtocolError("invalid_id", "response id must be a non-empty string")
    if not isinstance(ok, bool):
        raise ProtocolError("invalid_response", "response ok must be boolean", request_id)
    key = "result" if ok else "error"
    detail = value.get(key, {})
    if not isinstance(detail, Mapping):
        raise ProtocolError("invalid_response", f"response {key} must be an object", request_id)
    return request_id, ok, dict(detail)


__all__ = [
    "JsonLineDecoder",
    "MAX_LINE_BYTES",
    "ProtocolError",
    "decode_line",
    "encode_line",
    "require_request",
    "require_response",
]
