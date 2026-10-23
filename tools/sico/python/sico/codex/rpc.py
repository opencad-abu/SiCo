"""Bounded Codex stdio RPC for app-server and exec-server, independent of Qt."""

from __future__ import annotations

import json
import os
import queue
import select
import threading
from collections import deque

from cadai.json_values import strict_json
from .. import diagnostics as diagnostic_contract
from ..local_process import LocalProcess
from .rpc_failure import RpcFailureSupport
from .rpc_queue import (
    BoundedMessageQueue,
    MAX_RPC_QUEUE_BYTES,
    RpcPendingBuffer,
)
from .rpc_write import PipeWriter, WriteFailure, acquire, deadline_after, remaining

MAX_LINE = 16 * 1024 * 1024


class RpcError(RuntimeError):
    def __init__(self, message, *, phase=None, sent_bytes=None,
                 code="rpc_error", diagnostic_id=None):
        super().__init__(message)
        self.phase, self.sent_bytes = phase, sent_bytes
        self.code, self.diagnostic_id = code, diagnostic_id


class RpcRejected(RpcError):
    """A correlated error response, distinct from an uncertain transport failure."""

    def __init__(self, message, *, phase=None, sent_bytes=None,
                 code=None, diagnostic_id=None):
        super().__init__(
            message, phase=phase, sent_bytes=sent_bytes,
            code=code or diagnostic_contract.UPSTREAM_REJECTED,
            diagnostic_id=diagnostic_id,
        )


class AppServer(RpcPendingBuffer, RpcFailureSupport):
    def __init__(self, command, environment, cwd, *, cancelled=None, diagnostic=None,
                 protocol="app-server"):
        if protocol not in ("app-server", "exec-server"):
            raise ValueError("Unknown Codex RPC handshake")
        self._buffer_limit = 128 if protocol == "exec-server" else 4096
        self._frame_limit = 128 * 1024 if protocol == "exec-server" else MAX_LINE
        self.messages = BoundedMessageQueue(
            maxsize=self._buffer_limit, max_bytes=MAX_RPC_QUEUE_BYTES)
        self._init_pending()
        self.notification_handler = None
        self.counter = 0
        self.stopped = threading.Event()
        self.closed_by_client = False
        self.exit = None
        self.cancelled = cancelled
        self._request_lock = threading.Lock()
        self._failure_lock = threading.Lock()
        self._failure = None
        self._closed = False
        self._diagnostic = diagnostic or diagnostic_contract.DiagnosticLog.disabled(
            "codex")
        self.diagnostic_id = self._diagnostic.correlation_id
        self._process = LocalProcess(
            command, environment, cwd, diagnostic=self._diagnostic, component="codex")
        self.child = self._process.child
        self._diagnostic.emit("codex.app_server.started", stage="spawn",
                              pid=getattr(self.child, "pid", None))
        try:
            self._writer = PipeWriter(self.child.stdin, self.stopped)
        except BaseException:
            self._process.close()
            self.child.stdout.close()
            raise
        self.reader = threading.Thread(target=self._read, name="copilot-codex-reader")
        self.reader.start()
        try:
            self.initialization = self.request(
                "initialize",
                {"clientName": "sico-terminal"} if protocol == "exec-server" else {
                    "capabilities": {"experimentalApi": True},
                    "clientInfo": {
                        "name": "cad_copilot",
                        "title": "Silicon Copilot",
                        "version": "0.1.0",
                    },
                }, timeout=5 if protocol == "exec-server" else 30,
            )
            self.send({"method": "initialized", "params": {}},
                      timeout=5 if protocol == "exec-server" else 30)
        except BaseException:
            self.close()
            raise

    def _read(self):
        pending = bytearray()
        frame_limit = getattr(self, "_frame_limit", MAX_LINE)
        failure = None
        reached_eof = False
        try:
            descriptor = self.child.stdout.fileno()
            reader = select.poll()
            reader.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR)
            while not self.stopped.is_set():
                # A partial frame or an inherited writer must not prevent close.
                if not reader.poll(100):
                    continue
                raw = os.read(descriptor, 65536)
                if not raw:
                    reached_eof = True
                    break
                pending.extend(raw)
                while b"\n" in pending and not self.stopped.is_set():
                    end = pending.index(b"\n") + 1
                    if end > frame_limit:
                        failure = self.failure_record(
                            diagnostic_contract.PROTOCOL_FRAME_TOO_LARGE,
                            "Codex app-server frame exceeds limit",
                            "RpcError", frame_bytes=end,
                        )
                        raise RpcError("Invalid app-server frame")
                    try:
                        message = strict_json(pending[:end])
                    except (ValueError, UnicodeError) as exc:
                        failure = self.failure_record(
                            diagnostic_contract.PROTOCOL_FRAME_INVALID,
                            "Codex app-server frame is invalid",
                            type(exc).__name__, frame_bytes=end,
                        )
                        raise
                    del pending[:end]
                    while not self.stopped.is_set():
                        try:
                            put_sized = getattr(self.messages, "put_sized", None)
                            if callable(put_sized):
                                put_sized(message, end, timeout=0.1)
                            else:
                                self.messages.put(message, timeout=0.1)
                            break
                        except queue.Full:
                            continue
                if len(pending) >= frame_limit:
                    failure = self.failure_record(
                        diagnostic_contract.PROTOCOL_FRAME_TOO_LARGE,
                        "Codex app-server frame exceeds limit",
                        "RpcError", frame_bytes=len(pending),
                    )
                    raise RpcError("Invalid app-server frame")
        except OSError as exc:
            if failure is None and not self.stopped.is_set():
                failure = self.failure_record(
                    diagnostic_contract.TRANSPORT_READ_FAILED,
                    "Codex app-server read failed",
                    type(exc).__name__,
                )
        except (ValueError, UnicodeError, RpcError):
            pass
        finally:
            poll = getattr(self.child, "poll", None)
            if poll is not None:
                try:
                    self.exit = poll()
                except OSError:
                    self.exit = None
            observed_exit = getattr(
                getattr(self, "_process", None), "exit_code", None)
            if callable(observed_exit):
                actual = observed_exit()
                if actual is not None:
                    self.exit = actual
            if (failure is None and not self.closed_by_client
                    and not self.stopped.is_set()):
                if reached_eof and pending:
                    failure = self.failure_record(
                        diagnostic_contract.PROTOCOL_FRAME_TRUNCATED,
                        "Codex app-server frame ended before completion",
                        "ProtocolError", frame_bytes=len(pending),
                    )
                elif reached_eof:
                    observed = self.process_failure(.2)
                    failure = self.failure_record(
                        observed.get("code") or diagnostic_contract.PROCESS_EXIT,
                        self.disconnect_reason(),
                        "ProcessExit",
                    )
            if failure is not None:
                self.retain_failure(failure)
                self.emit_failure(failure)
            self.stopped.set()

    def send(self, message, *, timeout=30, cancelled=None):
        return self._send(message, deadline_after(timeout),
                          self.cancelled if cancelled is None else cancelled)

    def _send(self, message, deadline, cancelled):
        try:
            remaining(deadline, self.stopped, cancelled, "encode")
            raw = (json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n").encode()
            remaining(deadline, self.stopped, cancelled, "encode")
        except WriteFailure as exc:
            raise self._io_error(exc) from None
        if len(raw) > getattr(self, "_frame_limit", MAX_LINE):
            code = diagnostic_contract.PROTOCOL_FRAME_TOO_LARGE
            self.emit("codex.rpc.failed", category=code, stage="encode",
                      frame_bytes=len(raw), sent_bytes=0)
            raise RpcError(
                "Codex RPC frame exceeds limit" +
                diagnostic_contract.error_suffix(
                    code, getattr(self, "diagnostic_id", None)),
                phase="encode", sent_bytes=0, code=code,
                diagnostic_id=getattr(self, "diagnostic_id", None),
            )
        if self.child.poll() is not None:
            self.stopped.set()
            observed = self.process_failure(0)
            code = observed.get("code") or diagnostic_contract.PROCESS_EXIT
            self.emit(
                "codex.rpc.failed", category=code, stage="write",
                exception="ProcessExit", exit_code=observed.get("exit_code"),
                stderr=observed.get("stderr"), sent_bytes=0,
            )
            raise RpcError(
                self.disconnect_reason() +
                diagnostic_contract.error_suffix(
                    code, getattr(self, "diagnostic_id", None)),
                phase="write", sent_bytes=0, code=code,
                diagnostic_id=getattr(self, "diagnostic_id", None),
            )
        try:
            self._writer.write(raw, deadline, cancelled)
        except WriteFailure as exc:
            raise self._io_error(exc) from None
        return len(raw)

    def _io_error(self, exc):
        observed = self.process_failure(0)
        failure = getattr(self, "_failure", None)
        if str(exc) == "timed out":
            code = diagnostic_contract.TIMEOUT
        elif str(exc) == "cancelled":
            code = diagnostic_contract.CANCELLED
        elif str(exc) == "disconnected":
            code = (failure["code"] if failure else
                    observed.get("code") or diagnostic_contract.PROCESS_EXIT)
        else:
            code = diagnostic_contract.TRANSPORT_WRITE_FAILED
        reason = str(exc)
        if reason == "disconnected":
            reason = (self.failure_reason(failure) if failure is not None
                      else self.disconnect_reason())
        if exc.sent or self.stopped.is_set():
            self.retain_failure(self.failure_record(
                code, f"Codex RPC connection stopped after {exc.phase} {reason}",
                type(exc).__name__))
        if exc.sent:
            self.stopped.set()
        self.emit(
            "codex.rpc.failed", category=code, stage=exc.phase,
            exception=type(exc).__name__, exit_code=observed.get("exit_code"),
            stderr=observed.get("stderr"), sent_bytes=exc.sent,
        )
        message = f"Codex RPC {exc.phase} {reason}; sent_bytes={exc.sent}; no replay"
        return RpcError(
            message + diagnostic_contract.error_suffix(
                code, getattr(self, "diagnostic_id", None)),
            phase=exc.phase, sent_bytes=exc.sent, code=code,
            diagnostic_id=getattr(self, "diagnostic_id", None),
        )

    def _next(self, timeout):
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            if self.stopped.is_set():
                failure = getattr(self, "_failure", None)
                if failure is not None:
                    message = self.failure_reason(failure)
                    raise RpcError(
                        message + diagnostic_contract.error_suffix(
                            failure["code"], getattr(self, "diagnostic_id", None)),
                        phase="read", code=failure["code"],
                        diagnostic_id=getattr(self, "diagnostic_id", None),
                    ) from None
                raise RpcError(
                    self.disconnect_reason() + diagnostic_contract.error_suffix(
                        diagnostic_contract.PROCESS_EXIT,
                        getattr(self, "diagnostic_id", None)),
                    phase="read", code=diagnostic_contract.PROCESS_EXIT,
                    diagnostic_id=getattr(self, "diagnostic_id", None),
                ) from None
            raise

    def _notification(self, message):
        return ("id" not in message and self.notification_handler is not None
                and self.notification_handler(message))
    def _buffer_exceeded(self):
        raise RpcError("Codex notification buffer exceeded")

    def poll_notifications(self, *, deadline=None, cancelled=None):
        """Consume handled metadata only; caller must own the idle RPC consumer."""
        retained = deque()
        message = None
        try:
            while self.pending:
                if deadline is not None:
                    remaining(deadline, self.stopped, cancelled, "notifications")
                message = self.pending.popleft()
                if not self._notification(message):
                    retained.append(message)
                message = None
            for _ in range(self.messages.qsize()):
                if deadline is not None:
                    remaining(deadline, self.stopped, cancelled, "notifications")
                if len(retained) >= getattr(self, "_buffer_limit", 4096):
                    break
                try:
                    message = self.messages.get_nowait()
                except queue.Empty:
                    break
                if not self._notification(message):
                    retained.append(message)
                message = None
        finally:
            if message is not None:
                retained.append(message)
            retained.extend(self.pending)
            self._replace_pending(retained)

    def request(self, method, params, timeout=30, *, cancelled=None):
        deadline = deadline_after(timeout)
        cancelled = self.cancelled if cancelled is None else cancelled
        try:
            acquire(self._request_lock, deadline, self.stopped, cancelled, "request_slot")
        except WriteFailure as exc:
            raise self._io_error(exc) from None
        sent = 0
        try:
            self.poll_notifications(deadline=deadline, cancelled=cancelled)
            self.counter += 1
            request_id = self.counter
            sent = self._send({"id": request_id, "method": method, "params": params},
                              deadline, cancelled)
            return self._response(request_id, method, deadline, cancelled, sent)
        except WriteFailure as exc:
            raise self._io_error(exc) from None
        except RpcRejected:
            raise
        except BaseException as exc:
            if sent:
                self.retain_failure(self.failure_record(
                    exc.code if isinstance(exc, RpcError) else
                    diagnostic_contract.UNEXPECTED_EXCEPTION,
                    "Codex RPC connection stopped during response", type(exc).__name__))
                self.stopped.set()
                if isinstance(exc, RpcError):
                    raise RpcError(
                        f"Codex RPC response {exc}; sent_bytes={sent}; no replay",
                        phase="response", sent_bytes=sent, code=exc.code,
                        diagnostic_id=getattr(
                            self, "diagnostic_id", None) or exc.diagnostic_id,
                    ) from exc
            raise
        finally:
            self._request_lock.release()

    def _response(self, request_id, method, deadline, cancelled, sent):
        while True:
            # A complete response queued before EOF is still authoritative.
            stopped = self.stopped if self.messages.empty() else None
            wait = remaining(deadline, stopped, cancelled, "response", sent)
            try:
                message = self._next(wait)
            except queue.Empty:
                continue
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    # Protocol messages may include upstream request details or secrets.
                    detail = message["error"]
                    upstream_code = detail.get("code") if isinstance(detail, dict) else None
                    category = diagnostic_contract.classify_rpc_error(
                        upstream_code,
                        detail.get("message", "") if isinstance(detail, dict) else "",
                    )
                    self.emit(
                        "codex.rpc.rejected", category=category, stage=str(method)[:120],
                        exception="RpcRejected", rpc_code=upstream_code,
                        exit_code=self.exit,
                    )
                    raise RpcRejected(
                        f"Codex {method} failed (code {upstream_code})" +
                        diagnostic_contract.error_suffix(
                            category, getattr(self, "diagnostic_id", None)),
                        code=category,
                        diagnostic_id=getattr(self, "diagnostic_id", None),
                    )
                return message.get("result", {})
            try:
                if self._notification(message):
                    continue
            except Exception:
                self._retain_pending(message)
                raise
            if len(self.pending) >= getattr(self, "_buffer_limit", 4096):
                raise RpcError("Codex notification buffer exceeded")
            self._retain_pending(message)

    def poll_requests(self, handler):
        """Resolve supported idle requests; retain unrelated frames in their original order."""
        retained = deque()
        message = None
        try:
            remaining = len(self.pending) + self.messages.qsize()
            for _ in range(min(getattr(self, "_buffer_limit", 4096), remaining)):
                try:
                    message = self.pending.popleft() if self.pending else self.messages.get_nowait()
                except queue.Empty:
                    break
                if not ("id" in message and message.get("method") and handler(message)):
                    retained.append(message)
                message = None
        finally:
            if message is not None:
                retained.append(message)
            retained.extend(self.pending)
            self._replace_pending(retained)

    def observe_process(self, namespace_pid, namespace, executable):
        """Delegate host observations to the existing local process owner."""
        return self._process.observe_process(namespace_pid, namespace, executable)

    def close(self, *, require_confirmed=False):
        if getattr(self, "_closed", False):
            if getattr(self, "_cleanup_error", None) is not None:
                raise self._cleanup_error
            if require_confirmed:
                self._process.close(require_confirmed=True)
            return
        self._closed = True
        self.closed_by_client = True
        self.stopped.set()
        writer = getattr(self, "_writer", None)
        if writer is not None:
            writer.close()
        error = None
        try:
            if require_confirmed:
                self._process.close(require_confirmed=True)
            else:
                self._process.close()
        except (OSError, RuntimeError) as exc:
            error = exc
        finally:
            self.reader.join(timeout=.5)
            if self.reader.is_alive():
                code = diagnostic_contract.CLEANUP_UNCONFIRMED
                self.emit("codex.rpc.cleanup_unconfirmed",
                          category=code, stage="cleanup",
                          exception="ReaderThread", cleanup="unconfirmed")
                error = error or RpcError(
                    "Codex reader cleanup was not confirmed" +
                    diagnostic_contract.error_suffix(
                        code, getattr(self, "diagnostic_id", None)),
                    code=code, diagnostic_id=getattr(self, "diagnostic_id", None),
                )
            self.child.stdout.close()
        if error is not None:
            self._cleanup_error = error
            raise error
