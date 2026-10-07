"""Loopback Responses proxy. Only tool protocol changes; no retries or tool execution."""

from __future__ import annotations

import hmac
import http.client
import json
import secrets
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .provider_settings import provider_endpoint, validate_key
from .response_http_error import body as error_body, rejection, safe_id, summary
from .json_values import strict_json
from .responses_sse import transform_stream
from .responses_tools import CompatibilityError, ToolNames

MAX_BODY = 16 * 1024 * 1024


class _Exchange:
    """Close the upstream socket even after HTTPResponse takes ownership of it."""

    def __init__(self, wrapper, downstream):
        self.wrapper, self.downstream = wrapper, downstream
        self.connection = wrapper.connection()
        self.socket = None
        self.done = threading.Event()
        self.cancelled = threading.Event()
        self.watcher = threading.Thread(target=self.watch, name="copilot-wrapper-disconnect")

    def cancel(self):
        self.cancelled.set()
        if self.socket:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def watch(self):
        while not self.done.wait(0.1):
            try:
                readable, _, _ = select.select([self.downstream], [], [], 0)
                if self.wrapper.stopped.is_set() or (
                    readable and not self.downstream.recv(1, socket.MSG_PEEK)
                ):
                    self.cancel()
                    return
            except OSError:
                self.cancel()
                return

    def connect(self):
        self.watcher.start()
        self.connection.connect()
        self.socket = self.connection.sock
        self.socket.settimeout(self.wrapper.idle_timeout)
        if self.cancelled.is_set() or self.wrapper.stopped.is_set():
            self.cancel()
            raise OSError("Wrapper connection cancelled")
        return self.connection

    def close(self):
        self.done.set()
        self.cancel()
        self.connection.close()
        if self.watcher.ident:
            self.watcher.join(timeout=1)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def reply(self, code, body):
        encoded = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True

    def do_POST(self):  # noqa: N802
        wrapper = self.server.wrapper
        started = False
        exchange, response = None, None
        try:
            if not hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + wrapper.token
            ):
                self.reply(401, {"error": {"message": "Invalid local wrapper credential"}})
                return
            if self.path != "/v1/responses":
                self.reply(404, {"error": {"message": "Unsupported wrapper endpoint"}})
                return
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isdigit() or "Transfer-Encoding" in self.headers:
                raise CompatibilityError("A bounded Content-Length is required")
            size = int(lengths[0])
            if not 0 < size <= MAX_BODY:
                raise CompatibilityError("Responses request exceeds size limit")
            if self.headers.get("Content-Encoding", "identity") != "identity":
                raise CompatibilityError("Disable Codex request compression for the wrapper")
            body = self.rfile.read(size)
            if len(body) != size:
                raise CompatibilityError("Truncated Responses request")
            wrapper.diagnostic = ""
            wrapper.http_status = None
            names = ToolNames()
            request = names.request(strict_json(body))
            exchange = _Exchange(wrapper, self.connection)
            with wrapper.lock:
                if wrapper.stopped.is_set():
                    return
                wrapper.connections.add(exchange)
            upstream = exchange.connect()
            upstream.request(
                "POST",
                wrapper.url.path,
                body=json.dumps(request, ensure_ascii=False, allow_nan=False).encode(),
                headers={
                    "Authorization": "Bearer " + wrapper.api_key,
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream, application/json",
                    "Accept-Encoding": "identity",
                },
            )
            response = upstream.getresponse()
            if response.status != 200:
                details = rejection(response, (wrapper.api_key, wrapper.token))
                wrapper.http_status = response.status
                wrapper.diagnostic = summary(response.status, details)
                self.reply(response.status, error_body(response.status, details))
                return
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise CompatibilityError("Compressed upstream responses are unsupported")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip()
            if content_type == "application/json":
                raw = response.read(MAX_BODY + 1)
                if len(raw) > MAX_BODY:
                    raise CompatibilityError("Responses body exceeds size limit")
                self.reply(200, wrapper.response(names.response(strict_json(raw))))
                return
            if content_type != "text/event-stream":
                raise CompatibilityError("Gateway must return JSON or SSE")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            started = True
            for frame in transform_stream(
                response, lambda event: wrapper.event(names.event(event)), exchange.cancelled.is_set
            ):
                self.wfile.write(frame)
                self.wfile.flush()
        except (ValueError, KeyError, TypeError) as exc:
            message = str(exc) if isinstance(exc, CompatibilityError) else "Invalid tool protocol"
            self.failure(started, message)
        except (OSError, http.client.HTTPException):
            self.failure(started, "Model gateway connection interrupted")
        finally:
            if exchange:
                exchange.close()
                if response:
                    response.close()
                with wrapper.lock:
                    wrapper.connections.discard(exchange)

    def failure(self, started, message):
        self.server.wrapper.diagnostic = message
        try:
            if started:
                event = {"type": "error", "error": {"message": message}}
                self.wfile.write(b"event: error\ndata: " + json.dumps(event).encode() + b"\n\n")
                self.wfile.flush()
            else:
                self.reply(502, {"error": {"message": message}})
        except OSError:
            pass


class ResponsesWrapper:
    def __init__(self, base_url, api_key, *, timeout=30, idle_timeout=None):
        self.url = urlsplit(provider_endpoint(base_url, "responses"))
        validate_key(api_key)
        self.api_key, self.timeout = api_key, timeout
        self.idle_timeout = timeout if idle_timeout is None else idle_timeout
        self.diagnostic = ""
        self.http_status = None
        self.token = secrets.token_urlsafe(32)
        self.lock, self.stopped = threading.Lock(), threading.Event()
        self.connections = set()
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.wrapper = self
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def connection(self):
        factory = (
            http.client.HTTPSConnection
            if self.url.scheme == "https"
            else http.client.HTTPConnection
        )
        return factory(self.url.hostname, self.url.port, timeout=self.timeout)

    def request_id(self, value):
        return safe_id(value, (self.api_key, self.token))

    def response(self, response):
        # Diagnostic fields can echo request headers; leave tool and message data intact.
        if response.get("error") is not None:
            self.diagnostic = "Model gateway reported a Responses error"
            response["error"] = {"code": "server_error", "message": self.diagnostic}
        if response.get("incomplete_details") is not None:
            details = response["incomplete_details"]
            reason = details.get("reason") if isinstance(details, dict) else None
            response["incomplete_details"] = {
                "reason": reason if reason in ("max_output_tokens", "content_filter") else "unknown"
            }
        return response

    def event(self, event):
        if event.get("type") == "error":
            self.diagnostic = "Model gateway reported a Responses stream error"
            safe = {"type": "error", "code": "server_error", "message": self.diagnostic}
            if type(event.get("sequence_number")) is int:
                safe["sequence_number"] = event["sequence_number"]
            if "error" in event:
                safe["error"] = {"code": "server_error", "message": self.diagnostic}
            return safe
        if isinstance(event.get("response"), dict):
            event["response"] = self.response(event["response"])
        return event

    def close(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        with self.lock:
            for exchange in self.connections:
                exchange.cancel()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
