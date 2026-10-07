"""MCP JSON-RPC dispatch and bounded stdio framing."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, BinaryIO

from .protocol import MAX_LINE_BYTES, ProtocolError, decode_line, encode_line


def result(request_id: Any, value: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_message(
    message: dict[str, Any],
    *,
    instructions: Callable[[], str],
    list_tools: Callable[[], list[dict[str, Any]]],
    call_tool: Callable[[Any, Any], dict[str, Any]],
) -> dict[str, Any] | None:
    """Dispatch MCP messages using only the server's declared capabilities."""
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params", {})
    if method == "notifications/initialized" or (request_id is None and method):
        return None
    if method == "initialize":
        version = (
            params.get("protocolVersion", "2024-11-05")
            if isinstance(params, dict)
            else "2024-11-05"
        )
        return result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "cad-virtuoso", "version": "0.1.0"},
            "instructions": instructions(),
        })
    if method == "ping":
        return result(request_id, {})
    if method == "tools/list":
        return result(request_id, {"tools": list_tools()})
    if method == "tools/call":
        return call_tool(request_id, params)
    return error(request_id, -32601, f"method not found: {method}")


def serve(
    reader: BinaryIO,
    writer: BinaryIO,
    handle: Callable[[dict[str, Any]], dict[str, Any] | None],
    *,
    max_line_bytes: int = MAX_LINE_BYTES,
) -> None:
    """Process bounded JSONL input; lifetimes belong to the caller."""
    while raw := reader.readline(max_line_bytes + 2):
        if len(raw) > max_line_bytes + 1 or not raw.endswith(b"\n"):
            _discard_line(reader, raw, max_line_bytes)
            response = error(None, -32700, "MCP JSONL message exceeds the limit")
        else:
            try:
                response = handle(decode_line(raw[:-1]))
            except ProtocolError as exc:
                response = error(None, -32700, str(exc))
            except Exception as exc:
                response = error(None, -32603, f"internal error: {exc}")
        if response is not None:
            writer.write(encode_line(response))
            writer.flush()


def tool_result(
    request_id: Any, ok: bool, detail: dict[str, Any], *, compact: bool = False,
) -> dict[str, Any]:
    """Encode tool content and retain the existing oversized-response error."""
    text = json.dumps(detail, ensure_ascii=False, indent=None if compact else 2, sort_keys=True)
    response = result(request_id, {
        "content": [{"type": "text", "text": text}], "isError": not ok,
    })
    try:
        encode_line(response)
    except ProtocolError:
        from .skill_diagnostics import SkillDiagnostics

        diagnostics = SkillDiagnostics()
        diagnostics.add(detail)
        detail = diagnostics.attach({
            "code": "response_too_large",
            "message": "MCP tool response exceeds the JSONL limit; request less content",
        })
        return result(request_id, {
            "content": [{"type": "text", "text": json.dumps(detail, sort_keys=True)}],
            "isError": True,
        })
    return response


def _discard_line(reader: BinaryIO, first: bytes, max_line_bytes: int) -> None:
    chunk = first
    while chunk and not chunk.endswith(b"\n"):
        chunk = reader.readline(max_line_bytes + 2)


__all__ = ["error", "handle_message", "result", "serve", "tool_result"]
