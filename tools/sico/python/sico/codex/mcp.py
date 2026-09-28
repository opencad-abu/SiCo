"""Stdio MCP transport for the existing bound-context registry; no duplicate handlers."""

from __future__ import annotations

import hmac
import json
import os
import socket
import socketserver
import sys
import threading

from sicoenv import read as environment_setting

from ..transport.framing import Connection, strict_json


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(self.server.bridge.timeout)
        connection = Connection(self.request)
        try:
            message = connection.receive()
            if not hmac.compare_digest(str(message.get("token", "")), self.server.bridge.token):
                raise ValueError("Invalid tool transport credential")
            connection.send({"result": self.server.bridge.dispatch(message)})
        except (ValueError, TypeError, OSError, EOFError, KeyError):
            try:
                connection.send({"error": "CAD tool transport unavailable"})
            except OSError:
                pass


class ToolBridge:
    def __init__(self, tools, execute, *, timeout=1800):
        import secrets

        self.tools, self.execute, self.timeout = tools, execute, timeout
        self.token = secrets.token_urlsafe(32)
        # A parent turn may wait for a native child while that child is still
        # calling MCP.  A single-threaded server would deadlock in that case.
        class _ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = _ThreadedServer(("127.0.0.1", 0), _Handler)
        self.server.bridge = self
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}
        )
        self.thread.start()

    @property
    def port(self):
        return self.server.server_address[1]

    def dispatch(self, message):
        if message.get("method") == "list":
            return {
                "tools": [
                    {
                        "name": tool["name"],
                        "description": tool["description"],
                        "inputSchema": tool["input_schema"],
                        "annotations": tool["annotations"],
                    }
                    for tool in self.tools.schemas()
                ]
            }
        if message.get("method") == "call":
            metadata = message.get("metadata") or {}
            try:
                return self.execute(message["name"], message.get("arguments", {}), metadata=metadata)
            except TypeError as exc:
                # Keep the small bridge test/fallback API compatible with
                # legacy two-argument executors.
                if "metadata" not in str(exc):
                    raise
                return self.execute(message["name"], message.get("arguments", {}))
        raise ValueError("Unsupported CAD tool transport method")

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def forward(method, **params):
    try:
        timeout = float(environment_setting(os.environ, "SICO_MCP_TIMEOUT", "1800"))
    except ValueError:
        timeout = 1800.0
    if timeout < 1 or timeout > 3600:
        timeout = 1800.0
    with socket.create_connection(
        ("127.0.0.1", int(environment_setting(os.environ, "SICO_MCP_PORT", ""))), timeout
    ) as sock:
        sock.settimeout(timeout)
        connection = Connection(sock)
        connection.send(
            {"method": method, "token": environment_setting(os.environ, "SICO_MCP_TOKEN", ""), **params}
        )
        result = connection.receive()
        if "error" in result:
            raise ValueError("CAD tool transport unavailable")
        return result["result"]


def main():
    while True:
        raw = sys.stdin.buffer.readline(1024 * 1024 + 1)
        if not raw:
            return 0
        if len(raw) > 1024 * 1024 or not raw.endswith(b"\n"):
            return 2
        try:
            message = strict_json(raw)
        except ValueError:
            return 2
        request_id = message.get("id")
        if request_id is None:
            continue
        try:
            method, params = message.get("method"), message.get("params", {})
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "copilot-context", "version": "1.0"},
                }
            elif method == "tools/list":
                result = forward("list")
            elif method == "tools/call":
                result = forward(
                    "call", name=params["name"], arguments=params.get("arguments", {}),
                    metadata=params.get("_meta") or {},
                )
            elif method == "ping":
                result = {}
            else:
                raise ValueError("Unsupported MCP method")
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (ValueError, TypeError, OSError, EOFError, KeyError):
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32603,
                    "message": "CAD context tool request failed",
                },
            }
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
