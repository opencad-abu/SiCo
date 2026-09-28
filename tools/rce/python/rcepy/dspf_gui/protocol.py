"""Strict JSONL validation for the Virtuoso DSPF bridge."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping


MAX_LINE_BYTES = 65_536
MAX_NET_NAME_CHARS = 4_096
MAX_HIGHLIGHT_REGIONS = 256
MAX_ABS_COORDINATE = 1.0e15
REQUEST_METHODS = frozenset(
    {"oa.highlight_net", "oa.current_net", "gui.select_net", "bridge.ping"}
)
EVENT_METHODS = frozenset({"oa.selection_changed"})


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str, request_id: int | None = None):
        super().__init__(message)
        self.code = code
        self.request_id = request_id

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


def _request_id(value: Any, *, required: bool) -> int | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProtocolError("invalid_id", "id must be a positive integer")
    return value


def _params(value: Any, request_id: int | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ProtocolError("invalid_params", "params must be an object", request_id)
    return dict(value)


def _validate_name(params: Mapping[str, Any], request_id: int | None) -> None:
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise ProtocolError("invalid_params", "net name must be a non-empty string", request_id)
    if len(name) > MAX_NET_NAME_CHARS:
        raise ProtocolError("invalid_params", "net name exceeds the protocol limit", request_id)


def _validate_regions(
    params: dict[str, Any], request_id: int | None,
) -> None:
    raw = params.get("regions", [])
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_HIGHLIGHT_REGIONS:
        raise ProtocolError(
            "invalid_params",
            f"regions must be an array of at most {MAX_HIGHLIGHT_REGIONS} boxes",
            request_id,
        )
    regions: list[list[float]] = []
    for box in raw:
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise ProtocolError(
                "invalid_params", "each highlight region must contain four numbers",
                request_id,
            )
        values: list[float] = []
        for value in box:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ProtocolError(
                    "invalid_params", "highlight coordinates must be numbers", request_id,
                )
            number = float(value)
            if not math.isfinite(number) or abs(number) > MAX_ABS_COORDINATE:
                raise ProtocolError(
                    "invalid_params", "highlight coordinate is outside the finite limit",
                    request_id,
                )
            values.append(number)
        if values[0] > values[2] or values[1] > values[3]:
            raise ProtocolError(
                "invalid_params", "highlight region bounds are reversed", request_id,
            )
        regions.append(values)
    params["regions"] = regions


def _validate_request_params(
    method: str, params: dict[str, Any], request_id: int,
) -> None:
    allowed = {
        "oa.highlight_net": {"name", "regions"},
        "gui.select_net": {"name"},
        "oa.current_net": set(),
        "bridge.ping": set(),
    }[method]
    if unknown := set(params).difference(allowed):
        raise ProtocolError(
            "invalid_params", f"unsupported params field: {sorted(unknown)[0]}", request_id,
        )
    if method in {"oa.highlight_net", "gui.select_net"}:
        _validate_name(params, request_id)
    if method == "oa.highlight_net":
        _validate_regions(params, request_id)


def validate_message(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ProtocolError("invalid_message", "JSONL message must be an object")
    message = dict(payload)
    if "method" in message:
        request_id = _request_id(message.get("id"), required=True)
        method = message.get("method")
        if not isinstance(method, str):
            raise ProtocolError("invalid_method", "method must be a string", request_id)
        if method not in REQUEST_METHODS:
            raise ProtocolError("unknown_method", f"unsupported method: {method!r}", request_id)
        params = _params(message.get("params"), request_id)
        _validate_request_params(method, params, request_id)
        return {"id": request_id, "method": method, "params": params}
    if "event" in message:
        event = message.get("event")
        if not isinstance(event, str):
            raise ProtocolError("invalid_event", "event must be a string")
        if event not in EVENT_METHODS:
            raise ProtocolError("unknown_event", f"unsupported event: {event!r}")
        params = _params(message.get("params"), None)
        _validate_name(params, None)
        return {"event": event, "params": params}
    if "ok" in message:
        request_id = _request_id(message.get("id"), required=True)
        if not isinstance(message.get("ok"), bool):
            raise ProtocolError("invalid_response", "ok must be a boolean", request_id)
        if message["ok"]:
            result = message.get("result", {})
            if not isinstance(result, Mapping):
                raise ProtocolError(
                    "invalid_response", "successful response result must be an object", request_id
                )
            normalized = dict(result)
            if "name" in normalized:
                _validate_name(normalized, request_id)
            return {"id": request_id, "ok": True, "result": normalized}
        error = message.get("error")
        if (
            not isinstance(error, Mapping)
            or not isinstance(error.get("code"), str)
            or not isinstance(error.get("message"), str)
        ):
            raise ProtocolError(
                "invalid_response", "failed response requires an error object", request_id
            )
        return {"id": request_id, "ok": False, "error": dict(error)}
    raise ProtocolError("invalid_message", "message has no request, event, or response")


def decode_line(line: bytes | str) -> dict[str, Any]:
    raw = line.encode("utf-8") if isinstance(line, str) else line
    if len(raw) > MAX_LINE_BYTES:
        raise ProtocolError("line_too_long", "JSONL message exceeds 65536 bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid_encoding", "JSONL message must be UTF-8") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError("invalid_json", f"invalid JSON at column {exc.colno}") from exc
    return validate_message(payload)


def encode_message(payload: Mapping[str, Any]) -> str:
    message = validate_message(payload)
    text = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
    if len(text.encode("utf-8")) > MAX_LINE_BYTES:
        raise ProtocolError("line_too_long", "JSONL message exceeds 65536 bytes")
    return text


__all__ = ["ProtocolError", "decode_line", "encode_message", "validate_message"]
