"""Authenticated transport from the MCP child to cdns-ipc."""

from __future__ import annotations

import socket
import uuid
from pathlib import Path
from typing import Any

from .protocol import MAX_LINE_BYTES, ProtocolError, decode_line, encode_line, require_response
from .runtime import MAX_EVAL_BYTES, RuntimePaths, unix_socket_address, write_spool


class _BridgeRejectedError(ConnectionError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SocketClient:
    """Keep one authenticated bridge connection and issue bounded calls."""

    def __init__(self, path: Path, token: str, timeout: float, runtime: RuntimePaths):
        self.path = path
        self.token = token
        self.timeout = timeout
        self.runtime = runtime
        self._socket: socket.socket | None = None
        self._reader = None

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
        if self._socket is not None:
            self._socket.close()
        self._reader = None
        self._socket = None

    def connect(self) -> None:
        self._connect()

    def call_bounded(self, method, arguments, timeout):
        """Bound a startup observation independently of workflow timeouts."""
        original = self.timeout
        self.timeout = min(original, timeout)
        if self._socket is not None:
            self._socket.settimeout(self.timeout)
        try:
            return self.call(method, arguments)
        finally:
            self.timeout = original
            if self._socket is not None:
                self._socket.settimeout(original)

    def call(self, method: str, arguments: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        request_id = uuid.uuid4().hex
        owned_spool: str | None = None
        try:
            params, owned_spool = self._prepare_params(method, arguments)
            self._connect()
            assert self._socket is not None and self._reader is not None
            self._socket.sendall(
                encode_line({"id": request_id, "method": method, "params": params})
            )
            raw = self._reader.readline(MAX_LINE_BYTES + 2)
            if not raw or len(raw) > MAX_LINE_BYTES + 1 or not raw.endswith(b"\n"):
                raise ConnectionError(
                    "cdns-ipc disconnected; execution status is unknown and was not retried"
                )
            response_id, ok, detail = require_response(decode_line(raw[:-1]))
            if response_id != request_id:
                raise ConnectionError("cdns-ipc returned a mismatched request id")
            return ok, detail
        except (UnicodeError, ValueError) as exc:
            return False, {"code": "payload_too_large", "message": str(exc)}
        except _BridgeRejectedError as exc:
            self.close()
            return False, {"code": exc.code, "message": str(exc)}
        except (OSError, TimeoutError, ProtocolError) as exc:
            self.close()
            return False, {
                "code": "bridge_unavailable",
                "message": f"{exc}; the request was not retried",
            }
        finally:
            if owned_spool is not None:
                (self.runtime.spool / owned_spool).unlink(missing_ok=True)

    def _connect(self) -> None:
        if self._socket is not None:
            return
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(min(self.timeout, 10.0))
        with unix_socket_address(self.path) as address:
            connection.connect(address)
        reader = connection.makefile("rb")
        connection.sendall(encode_line({"type": "hello", "token": self.token}))
        raw = reader.readline(MAX_LINE_BYTES + 2)
        if not raw:
            reader.close()
            connection.close()
            raise ConnectionError("cdns-ipc rejected authentication")
        hello = decode_line(raw[:-1])
        if hello.get("ok") is not True:
            reader.close()
            connection.close()
            error = hello.get("error")
            if isinstance(error, dict) and error.get("code") == "bridge_in_use":
                message = error.get("message")
                raise _BridgeRejectedError(
                    "bridge_in_use",
                    message if isinstance(message, str) else "cdns-ipc is in use",
                )
            raise PermissionError("cdns-ipc authentication failed")
        connection.settimeout(self.timeout)
        self._socket = connection
        self._reader = reader

    def _prepare_params(
        self, method: str, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        if method not in {"eval_skill", "eval_skill_native"}:
            return arguments, None
        code = arguments.get("code")
        if not isinstance(code, str):
            return arguments, None
        encoded = code.encode("utf-8")
        if len(encoded) > MAX_EVAL_BYTES:
            raise ValueError(f"SKILL expression exceeds {MAX_EVAL_BYTES} UTF-8 bytes")
        if len(encoded) <= 32_768:
            return arguments, None
        reference = write_spool(self.runtime.spool, "mcp-source", encoded)
        name = str(reference["name"])
        return (
            {
                "source_spool": {
                    "name": name,
                    "size": reference["size"],
                    "sha256": reference["sha256"],
                }
            },
            name,
        )


__all__ = ["MAX_EVAL_BYTES", "SocketClient"]
