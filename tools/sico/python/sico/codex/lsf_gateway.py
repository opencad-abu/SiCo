"""Authenticated loopback HTTP relay; only the LSF worker accesses the model endpoint."""

import hmac
import json
import secrets
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cadai.response_http_error import summary
from .gateway_job import GatewayJob, launcher
from .gateway_relay import UpstreamError, relay
from .gateway_session import (
    Busy,
    SessionWorker,
    TemporaryWorker,
    WorkerError,
    max_age,
    reuse_enabled,
    session_idle,
)
from .gateway_wire import MAX_BODY


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
        done = threading.Event()
        disconnected = threading.Event()
        state = {"phase": "queue submission", "started": False, "resident": None,
                 "temporary": None}
        healthy = False

        def watch():
            while not done.wait(0.1):
                try:
                    if gateway.stopped.is_set() or (
                        select.select([self.connection], [], [], 0)[0]
                        and not self.connection.recv(1, socket.MSG_PEEK)
                    ):
                        disconnected.set()
                        if state["resident"] is not None:
                            state["resident"].cancel()
                        elif state["temporary"] is not None:
                            state["temporary"].close()
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
            connection = None
            if gateway.worker is not None:
                try:
                    connection = gateway.worker.acquire()
                    state["resident"] = gateway.worker
                    gateway.worker.note_request()
                except Busy:
                    connection = None
                except WorkerError:
                    gateway.worker.stats["worker_failures"] += 1
                    connection = None
            if connection is None:
                temporary = gateway.temporary_factory()
                state["temporary"] = temporary
                try:
                    connection = temporary.acquire()
                except WorkerError as exc:
                    gateway.diagnostic = str(exc)
                    raise
            relay(gateway, connection, body, self, disconnected, state)
            healthy = not disconnected.is_set()
        except UpstreamError as exc:
            gateway.diagnostic = str(exc)
            healthy = not disconnected.is_set()
            if not state["started"] and not disconnected.is_set():
                try:
                    self.reply(502, gateway.diagnostic)
                except OSError:
                    pass
        except (OSError, EOFError, ValueError, RuntimeError, KeyError):
            if gateway.http_status is not None and gateway.http_status != 200:
                gateway.diagnostic = summary(gateway.http_status, {}, "Upstream returned")
            elif not disconnected.is_set() and not gateway.diagnostic:
                gateway.diagnostic = "LSF AI gateway failed during " + state["phase"]
            if not state["started"] and not disconnected.is_set():
                try:
                    self.reply(
                        gateway.http_status
                        if gateway.http_status and gateway.http_status != 200
                        else 502,
                        gateway.diagnostic,
                    )
                except OSError:
                    pass
        finally:
            disconnected.set()
            try:
                if state["resident"] is not None:
                    state["resident"].release(healthy=healthy)
                elif state["temporary"] is not None:
                    state["temporary"].close()
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
    def __init__(self, settings, cwd, evidence, *, job_factory=GatewayJob,
                 auxiliary=False):
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
        self.auxiliary = auxiliary
        self.reuse = reuse_enabled(self.environment, auxiliary)
        self.worker = None
        self.stats = {}
        if self.reuse:
            self.worker = SessionWorker(
                self,
                job_factory,
                idle_timeout=session_idle(self.environment),
                max_age=max_age(self.environment),
            )
            self.stats = self.worker.stats
        self.temporary_factory = lambda: TemporaryWorker(self, job_factory)
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.gateway = self
        self.base_url = "http://127.0.0.1:%d/v1" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        if self.worker is not None:
            self.worker.prewarm()

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
        if self.worker is not None:
            self.worker.close()
        if self.cleanup_failed:
            raise RuntimeError("LSF gateway cleanup requires reconciliation")
