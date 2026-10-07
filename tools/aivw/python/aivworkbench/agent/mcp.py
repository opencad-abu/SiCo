"""Small JSON-RPC/JSONL dispatch boundary for AIVW tools.

This is deliberately narrower than a general MCP server: the transport owns
framing and request IDs while :class:`ToolBroker` remains the only component
that can authorize and execute a domain action.  No shell, dynamic code, or
network capability is introduced by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable, Mapping

from .protocol import (
    AgentError,
    ActionKind,
    ErrorCode,
    ProtocolError,
)
from .value_codec import ensure_json, freeze, redact, thaw
from .tool_broker import ToolBroker


JSONRPC_VERSION = "2.0"
MAX_RPC_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class RpcRequest:
    request_id: str | int
    method: str
    params: Mapping[str, Any]

    def __post_init__(self) -> None:
        if isinstance(self.request_id, bool) or not isinstance(self.request_id, (str, int)):
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "JSON-RPC request id is invalid")
        if not isinstance(self.method, str) or not self.method:
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "JSON-RPC method is required")
        if not isinstance(self.params, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "JSON-RPC params must be an object")
        safe_params = freeze(redact(self.params))
        object.__setattr__(self, "params", safe_params)
        ensure_json(safe_params)


@dataclass(frozen=True)
class RpcResponse:
    request_id: str | int | None
    result: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.result is not None and self.error is not None:
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "JSON-RPC response cannot contain result and error")
        if self.result is not None and not isinstance(self.result, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "JSON-RPC result must be an object")
        if self.error is not None and not isinstance(self.error, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "JSON-RPC error must be an object")
        if self.result is not None:
            safe_result = freeze(redact(self.result))
            object.__setattr__(self, "result", safe_result)
            ensure_json(safe_result)
        if self.error is not None:
            safe_error = freeze(redact(self.error))
            object.__setattr__(self, "error", safe_error)
            ensure_json(safe_error)
        if self.request_id is not None and (
            isinstance(self.request_id, bool) or not isinstance(self.request_id, (str, int))
        ):
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "JSON-RPC response id is invalid")

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": self.request_id}
        if self.error is not None:
            value["error"] = thaw(self.error)
        else:
            value["result"] = thaw(self.result or {})
        return value


class ToolRpcServer:
    """Dispatch ``tools/list`` and ``tools/call`` over JSON-RPC records."""

    def __init__(self, broker: ToolBroker, *, server_name: str = "aivw-agent", server_version: str = "1") -> None:
        self.broker = broker
        self.server_name = server_name
        self.server_version = server_version

    def handle(self, raw: str | bytes | Mapping[str, Any]) -> RpcResponse | None:
        request = _parse_request(raw)
        if request.method == "notifications/initialized":
            return None
        if request.method == "initialize":
            if request.params and not set(request.params).issubset({"protocolVersion", "capabilities", "clientInfo"}):
                unknown = sorted(str(key) for key in request.params if key not in {"protocolVersion", "capabilities", "clientInfo"})
                raise ProtocolError(ErrorCode.UNKNOWN_FIELD, "initialize contains unknown fields", {"fields": unknown})
            return RpcResponse(request.request_id, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": self.server_name, "version": self.server_version},
                "capabilities": {"tools": {}},
            })
        if request.method == "tools/list":
            if request.params:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tools/list does not accept params")
            return RpcResponse(request.request_id, {"tools": [
                {"name": item["name"], "description": "AIVW controlled domain action", "inputSchema": item["schema"] or {"type": "object"}}
                for item in self.broker.describe()
            ]})
        if request.method == "tools/call":
            return self._call(request)
        raise ProtocolError(ErrorCode.TOOL_NOT_FOUND, "method is not supported", {"method": request.method})

    def handle_jsonl(self, lines: Iterable[str | bytes]) -> list[str]:
        output: list[str] = []
        for line in lines:
            if not (line.strip() if isinstance(line, bytes) else str(line).strip()):
                continue
            try:
                response = self.handle(line)
                if response is not None:
                    output.append(_encode(response.to_dict()))
            except ProtocolError as exc:
                request_id = _best_effort_id(line)
                try:
                    output.append(
                        _encode(
                            RpcResponse(
                                request_id,
                                error=_rpc_error(_jsonrpc_code(exc), exc.message, exc.code),
                            ).to_dict()
                        )
                    )
                except ProtocolError:
                    # Error responses are deliberately tiny, but retain a
                    # final fixed fallback if a hostile exception detail ever
                    # exceeds the transport cap.
                    output.append(
                        _encode(
                            {
                                "jsonrpc": JSONRPC_VERSION,
                                "id": request_id,
                                "error": {"code": -32000, "message": "protocol error"},
                            }
                        )
                    )
        return output

    def _call(self, request: RpcRequest) -> RpcResponse:
        unknown = sorted(str(key) for key in request.params if key not in {"name", "arguments", "source_generation", "budget", "idempotency_key", "template_lock", "action_kind"})
        if unknown:
            raise ProtocolError(ErrorCode.UNKNOWN_FIELD, "tools/call contains unknown fields", {"fields": unknown})
        name = request.params.get("name")
        arguments = request.params.get("arguments", {})
        if not isinstance(name, str) or not name:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tools/call requires a tool name")
        if not isinstance(arguments, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tools/call arguments must be an object")
        # ToolBroker's action schema remains authoritative.  The RPC adapter
        # only maps the method parameters into one constrained action.
        from .protocol import Action

        kind_by_tool = {"plan_experiment": ActionKind.PLAN_EXPERIMENT.value, "submit_experiment": ActionKind.SUBMIT_EXPERIMENT.value, "propose_model": ActionKind.PROPOSE_MODEL.value, "request_revision": ActionKind.REQUEST_REVISION.value}
        kind = kind_by_tool.get(name)
        if kind is None:
            # A registered read-only/custom tool can still be called by using
            # an explicit action kind supplied by the controller, but arbitrary
            # kinds are never accepted.
            kind = request.params.get("action_kind")
        if not isinstance(kind, str) or kind in {ActionKind.FINISH.value, ActionKind.BLOCKED.value}:
            raise ProtocolError(ErrorCode.TOOL_NOT_ALLOWED, "tool is not mapped to an AIVW action", {"tool": name})
        action = Action(
            action_id="rpc-%s" % str(request.request_id),
            kind=kind,
            parent_event_id="rpc-%s" % str(request.request_id),
            expected_source_generation=str(request.params.get("source_generation", "")),
            budget=dict(request.params.get("budget", {})) if isinstance(request.params.get("budget", {}), Mapping) else {},
            params=dict(arguments, tool=name),
            idempotency_key=request.params.get("idempotency_key", "rpc-%s" % str(request.request_id)),
            template_lock=request.params.get("template_lock"),
        )
        result = self.broker.execute(action)
        if result.error is not None:
            return RpcResponse(request.request_id, error=_rpc_error(-32000, result.error.message, result.error.code, result.to_dict()))
        return RpcResponse(request.request_id, {"content": [{"type": "json", "json": result.to_dict()}], "isError": False})


def _parse_request(raw: str | bytes | Mapping[str, Any]) -> RpcRequest:
    if isinstance(raw, Mapping):
        value = dict(raw)
    else:
        if isinstance(raw, bytes) and len(raw) > MAX_RPC_BYTES:
            raise ProtocolError(ErrorCode.CONTEXT_LIMIT_EXCEEDED, "JSON-RPC request exceeds hard cap")
        if isinstance(raw, str):
            try:
                if len(raw.encode("utf-8")) > MAX_RPC_BYTES:
                    raise ProtocolError(ErrorCode.CONTEXT_LIMIT_EXCEEDED, "JSON-RPC request exceeds hard cap")
            except UnicodeError as exc:
                raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "JSON-RPC request is not valid UTF-8") from exc
        try:
            value = json.loads(
                raw.decode("utf-8") if isinstance(raw, bytes) else raw,
                parse_constant=_reject_constant,
                object_pairs_hook=_reject_duplicate_object_names,
            )
        except (TypeError, ValueError, UnicodeError, _DuplicateObjectName) as exc:
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "invalid JSON-RPC request") from exc
    if not isinstance(value, Mapping):
        raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "JSON-RPC request must be an object")
    if value.get("jsonrpc") != JSONRPC_VERSION:
        raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "JSON-RPC version is unsupported")
    if "method" not in value or not isinstance(value["method"], str) or not value["method"]:
        raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "JSON-RPC method is required")
    request_id = value.get("id")
    if request_id is None:
        # Notifications have no response id.  Only the standard initialized
        # notification is accepted; arbitrary no-id calls are rejected so a
        # caller cannot hide an action result from the audit stream.
        if value["method"] == "notifications/initialized":
            request_id = "notification"
        else:
            raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "JSON-RPC request id is invalid")
    elif isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        raise ProtocolError(ErrorCode.INVALID_ENVELOPE, "JSON-RPC request id is invalid")
    params = value.get("params", {})
    if not isinstance(params, Mapping):
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "JSON-RPC params must be an object")
    unknown = [key for key in value if not isinstance(key, str) or key not in {"jsonrpc", "id", "method", "params"}]
    if unknown:
        raise ProtocolError(
            ErrorCode.UNKNOWN_FIELD,
            "JSON-RPC request contains unknown fields",
            {"fields": sorted(str(item) for item in unknown)},
        )
    return RpcRequest(request_id, value["method"], dict(params))


def _best_effort_id(raw: str | bytes) -> str | int | None:
    try:
        value = json.loads(
            raw.decode("utf-8") if isinstance(raw, bytes) else raw,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_object_names,
        )
        candidate = value.get("id") if isinstance(value, Mapping) else None
        return candidate if isinstance(candidate, (str, int)) and not isinstance(candidate, bool) else None
    except (TypeError, ValueError, UnicodeError, _DuplicateObjectName):
        return None


class _DuplicateObjectName(ValueError):
    pass


def _reject_duplicate_object_names(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateObjectName("duplicate JSON-RPC object field: %s" % key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant: %s" % value)


def _rpc_error(code: int, message: str, aivw_code: str, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"code": code, "message": message, "data": {"aivw_code": aivw_code}}
    if data:
        value["data"]["broker"] = dict(data)
    return value


def _jsonrpc_code(error: ProtocolError) -> int:
    """Map protocol failures to the standard JSON-RPC error boundary."""
    if error.code == ErrorCode.INVALID_ENVELOPE.value:
        return -32600
    if error.code == ErrorCode.TOOL_NOT_FOUND.value:
        return -32601
    if error.code in {
        ErrorCode.INVALID_ARGUMENTS.value,
        ErrorCode.UNKNOWN_FIELD.value,
        ErrorCode.INVALID_ACTION.value,
        ErrorCode.STALE_SOURCE_GENERATION.value,
        ErrorCode.TEMPLATE_LOCK_MISMATCH.value,
    }:
        return -32602
    return -32000


def _encode(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_RPC_BYTES:
        raise ProtocolError(ErrorCode.CONTEXT_LIMIT_EXCEEDED, "JSON-RPC response exceeds hard cap")
    return encoded


__all__ = ["JSONRPC_VERSION", "MAX_RPC_BYTES", "RpcRequest", "RpcResponse", "ToolRpcServer"]
