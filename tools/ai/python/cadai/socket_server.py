"""Authenticated Unix socket used between an agent's MCP child and the controller."""

from __future__ import annotations

import hmac
import os
import socket
import socketserver
import struct
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .protocol import MAX_LINE_BYTES, ProtocolError, decode_line, encode_line, require_request
from .runtime import unix_socket_address

MAX_SEEN_REQUEST_IDS = 4_096
MAX_AUTHENTICATED_CLIENTS = 8


class RequestFailure(RuntimeError):
    def __init__(self, code: str, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.data = data or {}

    def as_error(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "data": self.data}


Dispatch = Callable[[str, str, dict[str, Any]], dict[str, Any]]


class _Handler(socketserver.StreamRequestHandler):
    server: UnixRequestServer

    def handle(self) -> None:
        if not self.server.peer_is_current_user(self.request):
            return
        self.request.settimeout(self.server.authentication_timeout)
        hello = self._read_message()
        if hello is None or hello.get("type") != "hello":
            return
        token = hello.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token, self.server.token):
            self._write({"ok": False, "error": {"code": "unauthorized", "message": "bad token"}})
            return
        if not self.server.claim_authenticated_client():
            self._write(
                {
                    "ok": False,
                    "error": {
                        "code": "bridge_in_use",
                        "message": "the session MCP client limit was reached",
                    },
                }
            )
            return
        try:
            if not self._write({"ok": True, "result": {"authenticated": True}}):
                return
            self.request.settimeout(None)
            while True:
                message = self._read_message()
                if message is None:
                    return
                request_id: str | None = None
                request_active = False
                try:
                    request_id, method, params = require_request(message)
                    if not self.server.claim_request_id(request_id):
                        raise RequestFailure("duplicate_id", "request id was already used")
                    if not self.server.begin_request():
                        return
                    request_active = True
                    with self.server.execution_lock:
                        result = self.server.dispatch(request_id, method, params)
                    response = {"id": request_id, "ok": True, "result": result}
                except RequestFailure as exc:
                    response = {
                        "id": request_id or "invalid",
                        "ok": False,
                        "error": exc.as_error(),
                    }
                except ProtocolError as exc:
                    response = {
                        "id": request_id or exc.request_id or "invalid",
                        "ok": False,
                        "error": exc.as_error(),
                    }
                except Exception as exc:  # one response for every accepted request
                    response = {
                        "id": request_id or "invalid",
                        "ok": False,
                        "error": {"code": "internal_error", "message": str(exc)},
                    }
                try:
                    if not self._write(response):
                        return
                finally:
                    if request_active:
                        self.server.end_request()
        finally:
            self.server.release_authenticated_client()

    def _read_message(self) -> dict[str, Any] | None:
        try:
            raw = self.rfile.readline(MAX_LINE_BYTES + 2)
        except (OSError, TimeoutError):
            return None
        if not raw:
            return None
        if len(raw) > MAX_LINE_BYTES + 1 or not raw.endswith(b"\n"):
            return None
        try:
            return decode_line(raw[:-1])
        except ProtocolError:
            return None

    def _write(self, value: dict[str, Any]) -> bool:
        try:
            self.wfile.write(encode_line(value))
            self.wfile.flush()
            return True
        except (OSError, ProtocolError):
            return False


class UnixRequestServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        path: Path,
        token: str,
        dispatch: Dispatch,
        *,
        maximum_authenticated_clients: int = MAX_AUTHENTICATED_CLIENTS,
    ):
        if maximum_authenticated_clients <= 0:
            raise ValueError("maximum authenticated clients must be positive")
        self.path = path
        self.token = token
        self.dispatch = dispatch
        self.authentication_timeout = 10.0
        self.execution_lock = threading.Lock()
        self._activity = threading.Condition()
        self._active = 0
        self._accepting_requests = True
        self._seen_ids: set[str] = set()
        self._seen_order: deque[str] = deque()
        self.maximum_authenticated_clients = maximum_authenticated_clients
        self._authenticated_clients = 0
        with unix_socket_address(path) as address:
            super().__init__(address, _Handler)
        os.chmod(path, 0o600)

    def peer_is_current_user(self, connection: socket.socket) -> bool:
        if not hasattr(socket, "SO_PEERCRED"):
            return True
        try:
            credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _pid, uid, _gid = struct.unpack("3i", credentials)
        except OSError:
            return False
        return uid == os.getuid()

    def claim_request_id(self, request_id: str) -> bool:
        with self._activity:
            if request_id in self._seen_ids:
                return False
            if len(self._seen_order) >= MAX_SEEN_REQUEST_IDS:
                expired = self._seen_order.popleft()
                self._seen_ids.remove(expired)
            self._seen_ids.add(request_id)
            self._seen_order.append(request_id)
            return True

    def claim_authenticated_client(self) -> bool:
        with self._activity:
            if self._authenticated_clients >= self.maximum_authenticated_clients:
                return False
            self._authenticated_clients += 1
            return True

    def release_authenticated_client(self) -> None:
        with self._activity:
            if self._authenticated_clients <= 0:
                raise RuntimeError("authenticated client count underflow")
            self._authenticated_clients -= 1
            self._activity.notify_all()

    def wait_authenticated_clients(self, count: int, timeout: float) -> bool:
        with self._activity:
            return self._activity.wait_for(
                lambda: self._authenticated_clients == count,
                timeout=timeout,
            )

    def begin_request(self) -> bool:
        with self._activity:
            if not self._accepting_requests:
                return False
            self._active += 1
            return True

    def end_request(self) -> None:
        with self._activity:
            self._active -= 1
            self._activity.notify_all()

    def wait_idle(self, timeout: float) -> bool:
        with self._activity:
            return self._activity.wait_for(lambda: self._active == 0, timeout=timeout)

    def quiesce(self) -> None:
        with self._activity:
            self._accepting_requests = False

    def server_close(self) -> None:
        super().server_close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "MAX_AUTHENTICATED_CLIENTS",
    "RequestFailure",
    "UnixRequestServer",
]
