"""Authenticated loopback HTTP relay; only the LSF worker accesses the model endpoint."""

import hmac
import json
import secrets
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cadai.response_http_error import (MAX_ERROR, body as error_body, fields, safe_id, summary)
from .gateway_job import GatewayJob, launcher
from .gateway_wire import Frames, MAX_BODY, chunk, decode_data, encode, send


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def reply(self, status, message, *, payload=None, request_id=""):
        raw = json.dumps(payload or dict(error=dict(message=message))).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if request_id:
            self.send_header("x-request-id", request_id)
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(raw)

    def do_POST(self):  # noqa: N802
        gateway = self.server.gateway
        job = None
        started = False
        upstream_status = None
        phase = "queue submission"
        done = threading.Event()
        disconnected = threading.Event()

        def watch():
            while not done.wait(0.1):
                try:
                    if gateway.stopped.is_set() or (
                        select.select([self.connection], [], [], 0)[0]
                        and not self.connection.recv(1, socket.MSG_PEEK)
                    ):
                        disconnected.set()
                        if job and job.process:
                            job.process.close()
                        return
                except (OSError, RuntimeError, ValueError):
                    disconnected.set()
                    return

        watcher = threading.Thread(target=watch, daemon=True)
        try:
            self.connection.settimeout(10)
            if not hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + gateway.token
            ):
                self.reply(401, "Invalid local gateway credential")
                return
            if self.path != "/v1/responses":
                self.reply(404, "Unsupported gateway endpoint")
                return
            lengths = self.headers.get_all("Content-Length", [])
            if (
                len(lengths) != 1
                or not lengths[0].isdigit()
                or not 0 < int(lengths[0]) <= MAX_BODY
                or "Transfer-Encoding" in self.headers
                or self.headers.get("Content-Encoding", "identity") != "identity"
            ):
                self.reply(400, "A bounded uncompressed request is required")
                return
            body = self.rfile.read(int(lengths[0]))
            if len(body) != int(lengths[0]):
                raise ValueError("Truncated request")
            with gateway.lock:
                if gateway.stopped.is_set():
                    raise OSError("Gateway stopped")
                gateway.active.add(done)
            gateway.diagnostic = ""
            gateway.http_status = None
            watcher.start()
            job = gateway.job_factory(
                gateway.environment, gateway.cwd, gateway.evidence
            )
            phase = "worker handshake"
            reader = Frames(job.process.child.stdout)
            if (
                reader.next(gateway.start_timeout, disconnected.is_set).get("kind")
                != "ready"
            ):
                raise ValueError("Worker did not become ready")
            output = job.process.child.stdin
            send(
                output,
                encode(
                    "request",
                    endpoint=gateway.endpoint,
                    key=gateway.api_key,
                    timeout=gateway.timeout,
                    idle=gateway.idle_timeout,
                ),
            )
            for start in range(0, len(body), 4096):
                if disconnected.is_set():
                    raise OSError("Client disconnected")
                send(output, chunk(body[start : start + 4096]))
            send(output, encode("end"))
            phase = "upstream HTTP"
            headers = reader.next(
                gateway.timeout + gateway.idle_timeout, disconnected.is_set
            )
            if (
                headers.get("kind") != "headers"
                or type(headers.get("status")) is not int
                or not 100 <= headers["status"] <= 599
                or headers.get("content")
                not in {"text/event-stream", "application/json"}
            ):
                raise ValueError("Worker upstream failed")
            upstream_status = headers["status"]
            if upstream_status != 200:
                # Complete the bounded error exchange before publishing its
                # status. The flat client may close as soon as it sees 4xx.
                gateway.http_status = upstream_status
                gateway.diagnostic = summary(upstream_status, {}, "Upstream returned")
                raw = bytearray()
                while True:
                    row = reader.next(gateway.idle_timeout, disconnected.is_set)
                    if row.get("kind") == "end":
                        break
                    if row.get("kind") != "data":
                        raise ValueError("Incomplete worker error response")
                    raw.extend(decode_data(row))
                    if len(raw) > MAX_ERROR:
                        raise ValueError("Worker error exceeds limit")
                details = fields(raw, (gateway.api_key, gateway.token))
                identity = safe_id(headers.get("request_id", ""), (gateway.api_key, gateway.token))
                if identity:
                    details["request_id"] = identity
                gateway.diagnostic = summary(upstream_status, details, "Upstream returned")
                self.reply(upstream_status, "", payload=error_body(upstream_status, details),
                           request_id=identity)
                return
            phase = "response stream"
            self.send_response(headers["status"])
            self.send_header("Content-Type", headers["content"])
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            started = True
            while True:
                row = reader.next(gateway.idle_timeout, disconnected.is_set)
                if row.get("kind") == "end":
                    break
                if row.get("kind") != "data":
                    raise ValueError("Remote upstream interrupted")
                self.wfile.write(decode_data(row))
                self.wfile.flush()
        except (OSError, EOFError, ValueError, RuntimeError, KeyError):
            if upstream_status is not None and upstream_status != 200:
                gateway.diagnostic = summary(upstream_status, {}, "Upstream returned")
            elif not disconnected.is_set():
                gateway.diagnostic = "LSF AI gateway failed during " + phase
            if not started and not disconnected.is_set():
                try:
                    self.reply(upstream_status if upstream_status and upstream_status != 200 else 502,
                               gateway.diagnostic)
                except OSError:
                    pass
        finally:
            disconnected.set()
            try:
                if job:
                    job.close()
            except (OSError, RuntimeError, TimeoutError):
                gateway.diagnostic = (
                    "LSF AI job cleanup unconfirmed; inspect codex/lsf-gateway evidence"
                )
                gateway.cleanup_failed = True
            finally:
                done.set()
                if watcher.ident:
                    watcher.join(timeout=1)
                with gateway.lock:
                    gateway.active.discard(done)


class LsfGateway:
    def __init__(self, settings, cwd, evidence, *, job_factory=GatewayJob):
        launcher(settings.environment)
        self.endpoint, self.api_key = settings.endpoint, settings.api_key
        self.timeout, self.idle_timeout = settings.timeout, settings.idle_timeout
        self.environment = dict(settings.environment)
        self.environment.pop(getattr(settings, "options", {}).get("api_key_env", "SICO_API_KEY"), None)
        self.start_timeout = 120
        self.cwd, self.evidence, self.job_factory = cwd, evidence, job_factory
        self.token = secrets.token_hex(32)
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.active = set()
        self.diagnostic = ""
        self.http_status = None
        self.cleanup_failed = False
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.gateway = self
        self.base_url = "http://127.0.0.1:%d/v1" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        with self.lock:
            active = list(self.active)
        for done in active:
            if not done.wait(15):
                self.cleanup_failed = True
        if self.cleanup_failed:
            raise RuntimeError("LSF gateway cleanup requires reconciliation")
