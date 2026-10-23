"""Translate one local Responses request into worker frames and HTTP output."""

from cadai.response_http_error import MAX_ERROR, body as error_body, fields, safe_id, summary
from .gateway_session import WorkerError
from .gateway_wire import chunk, decode_data, encode


class UpstreamError(RuntimeError):
    """The worker remained healthy but its upstream HTTP exchange failed."""


def _drain_end(reader, timeout, disconnected):
    while True:
        row = reader.next(timeout, disconnected.is_set)
        if row.get("kind") == "end":
            return
        if row.get("kind") != "data":
            raise WorkerError("Worker error response interrupted")


def relay(gateway, connection, body, handler, disconnected, state):
    connection.send(
        encode(
            "request",
            endpoint=gateway.endpoint,
            key=gateway.api_key,
            timeout=gateway.timeout,
            idle=gateway.idle_timeout,
        )
    )
    for start in range(0, len(body), 4096):
        if disconnected.is_set():
            raise OSError("Client disconnected")
        connection.send(chunk(body[start : start + 4096]))
    connection.send(encode("end"))
    state["phase"] = "upstream HTTP"
    headers = connection.reader.next(
        gateway.timeout + gateway.idle_timeout, disconnected.is_set
    )
    if headers.get("kind") == "error":
        try:
            _drain_end(connection.reader, gateway.idle_timeout, disconnected)
        except (OSError, EOFError, ValueError):
            pass
        raise UpstreamError(str(headers.get("message", "LSF AI worker failed")))
    if (
        headers.get("kind") != "headers"
        or type(headers.get("status")) is not int
        or not 100 <= headers["status"] <= 599
        or headers.get("content") not in {"text/event-stream", "application/json"}
    ):
        raise WorkerError("Worker upstream failed")
    status = headers["status"]
    if status != 200:
        gateway.http_status = status
        gateway.diagnostic = summary(status, {}, "Upstream returned")
        raw = bytearray()
        while True:
            row = connection.reader.next(gateway.idle_timeout, disconnected.is_set)
            if row.get("kind") == "end":
                break
            if row.get("kind") != "data":
                raise WorkerError("Incomplete worker error response")
            raw.extend(decode_data(row))
            if len(raw) > MAX_ERROR:
                raise WorkerError("Worker error exceeds limit")
        details = fields(raw, (gateway.api_key, gateway.token))
        identity = safe_id(headers.get("request_id", ""), (gateway.api_key, gateway.token))
        if identity:
            details["request_id"] = identity
        gateway.diagnostic = summary(status, details, "Upstream returned")
        handler.reply(
            status, "", payload=error_body(status, details), request_id=identity
        )
        return
    state["phase"] = "response stream"
    handler.send_response(status)
    handler.send_header("Content-Type", headers["content"])
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.close_connection = True
    state["started"] = True
    while True:
        row = connection.reader.next(gateway.idle_timeout, disconnected.is_set)
        if row.get("kind") == "end":
            break
        if row.get("kind") != "data":
            raise WorkerError("Remote upstream interrupted")
        handler.wfile.write(decode_data(row))
        handler.wfile.flush()
