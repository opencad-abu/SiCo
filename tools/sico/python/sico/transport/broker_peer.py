"""One authenticated relay peer and its request/reply mailboxes."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout

from ..core.contracts import BoundContext, CircuitCallError, NeedsReconcile, identifier
from .circuit import METHOD as CIRCUIT_METHOD
from .framing import VERSION, Connection, ProtocolError
from .methods import ASSISTANT_METHOD, SHARED_READ_METHODS, QueryUnavailable
from .relay import circuit_diagnostic, emit_diagnostic


class Peer:
    def __init__(self, connection: Connection, hello: dict, diagnostic=None, *,
                 router=None, operation_timeout=1805, preserve_router=False):
        self.connection = connection
        self.instance_id = identifier(hello["instance_id"])
        self.generation = identifier(hello["generation"])
        self.contexts = {
            identifier(c["target_id"]): BoundContext.from_record(c) for c in hello["contexts"]
        }
        if (
            not self.contexts
            or len(self.contexts) != len(hello["contexts"])
            or any(
                c.instance_id != self.instance_id or c.generation != self.generation
                for c in self.contexts.values()
            )
        ):
            raise ProtocolError("Invalid context registration")
        self.lock = threading.Lock()
        self.diagnostic = diagnostic
        self.router = router
        self.operation_timeout = operation_timeout
        self._mailboxes = {}
        self._expired = set()
        self._mailbox_lock = threading.Lock()
        self._failure = None
        self._reader = None
        self._async_replies = hasattr(connection, "socket")
        self._close_router_on_failure = True
        self._preserve_router = preserve_router

    def demote(self):
        """Drain existing replies but no longer own the generation's lifetime."""
        with self._mailbox_lock:
            self._close_router_on_failure = False

    def start(self):
        if not self._async_replies:
            return
        self._reader = threading.Thread(target=self._receive, name="skill-replies", daemon=True)
        self._reader.start()

    def _receive(self):
        connection = self.connection
        try:
            while True:
                reply = connection.receive()
                if (reply.get("protocol") != VERSION
                        or reply.get("generation") != self.generation
                        or ("instance_id" in reply and reply["instance_id"] != self.instance_id)):
                    raise ProtocolError("Reply belongs to another generation")
                request_id = reply.get("id")
                if reply.get("kind") == "skill_late_reply":
                    with self._mailbox_lock:
                        matched = request_id in self._expired
                        self._expired.discard(request_id)
                    if matched and self.router:
                        if self.router.record_reply(request_id, reply.get("reply", {"id": request_id,
                                                    "ok": reply.get("ok")}), late=True):
                            self.router.clear_unknown(request_id)
                        else:
                            with self._mailbox_lock:
                                self._expired.add(request_id)
                    continue
                if reply.get("kind") != "response":
                    raise ProtocolError("Unexpected relay message")
                unknown_notice = reply.get("code") in {
                    "skill_timeout", "skill_response_pending", "skill_reply_write_failed"}
                with self._mailbox_lock:
                    future = self._mailboxes.get(request_id)
                    expired = request_id in self._expired
                    if expired and not unknown_notice:
                        self._expired.discard(request_id)
                if unknown_notice:
                    if future is None and not expired:
                        continue
                    with self._mailbox_lock:
                        self._expired.add(request_id)
                    if self.router:
                        self.router.mark_unknown(request_id)
                    if future is not None and not future.done():
                        future.set_result(reply)
                    continue
                if future is None:
                    # The caller may have timed out and removed its mailbox while
                    # Virtuoso was still evaluating the request.  Relay wraps
                    # this case as skill_late_reply; tolerate an unwrapped late
                    # response as well and quarantine it by request id instead
                    # of taking down the shared instance connection.
                    emit_diagnostic(
                        self.diagnostic,
                        "router.late_reply",
                        request_id=request_id,
                        instance_id=self.instance_id,
                        generation=self.generation,
                    )
                    if expired and self.router:
                        self.router.record_reply(request_id, reply, late=True)
                        self.router.clear_unknown(request_id)
                    continue
                if self.router:
                    self.router.record_reply(request_id, reply, late=expired)
                future.set_result(reply)
                if expired and self.router:
                    self.router.clear_unknown(request_id)
        except (OSError, EOFError, ValueError, ProtocolError, QueryUnavailable) as exc:
            with self._mailbox_lock:
                self._failure = exc
                pending = [(request_id, future) for request_id, future in self._mailboxes.items()
                           if not future.done()]
                if self.router and (self._preserve_router or not self._close_router_on_failure):
                    for request_id, future in pending:
                        try:
                            self.router.mark_unknown(request_id)
                        except QueryUnavailable:
                            self.router.close()
                            break
                for request_id, future in pending:
                    future.set_exception(exc)
                if (self.router and self.connection is connection
                        and self._close_router_on_failure and not self._preserve_router):
                    self.router.close()

    def close(self, *, close_router=True):
        # A generation router can be shared by multiple authenticated
        # connections during same-generation reconnect.  Detaching an old
        # connection must not close that shared router, including from its
        # reader thread after the socket is closed.
        with self._mailbox_lock:
            self._close_router_on_failure = False
        if close_router and self.router:
            self.router.close()
        self.connection.close()
        if self._reader and self._reader is not threading.current_thread():
            self._reader.join(timeout=1)

    def call(
        self, context: BoundContext, method: str = "get_context", params=None, request_id=None,
        native_identity=None
    ) -> dict:
        bound = self.contexts.get(context.target_id)
        if bound is None or context.record() != bound.record():
            raise NeedsReconcile("Bound context is no longer available")
        request_id = request_id or uuid.uuid4().hex
        with self.lock:
            if not hasattr(self.connection, "socket"):
                self.connection.send({
                    "protocol": VERSION, "kind": "request", "id": request_id,
                    "instance_id": self.instance_id, "generation": self.generation,
                    "method": method, "target_id": context.target_id,
                    **({"params": params} if params is not None else {}),
                    **({"native_identity": native_identity} if native_identity else {}),
                })
                return self._validate_reply(self.connection.receive(), request_id, method, context)
            future = Future()
            with self._mailbox_lock:
                if self._failure:
                    raise self._failure
                self._mailboxes[request_id] = future
            try:
                return self._call(context, method, params, request_id, future, native_identity)
            finally:
                with self._mailbox_lock:
                    self._mailboxes.pop(request_id, None)

    def _validate_reply(self, reply, request_id, method, context):
        if (reply.get("protocol") != VERSION or reply.get("kind", "response") != "response"
                or reply.get("id") != request_id or reply.get("generation") != self.generation):
            raise ProtocolError("Response does not match request")
        if not reply.get("ok"):
            self._raise_reply_error(reply, method, context)
        result = reply.get("result")
        if not isinstance(result, dict):
            raise ProtocolError("Context result must be an object")
        return result

    def _call(self, context, method, params, request_id, future, native_identity=None):
        self.connection.send(
            {
                "protocol": VERSION,
                "kind": "request",
                "id": request_id,
                "instance_id": self.instance_id,
                "generation": self.generation,
                "method": method,
                "target_id": context.target_id,
                **({"params": params} if params is not None else {}),
                **({"native_identity": native_identity} if native_identity else {}),
            }
        )
        emit_diagnostic(
            self.diagnostic,
            "router.dispatch",
            request_id=request_id,
            method=method,
            function=(params or {}).get("function"),
            instance_id=self.instance_id,
            generation=self.generation,
            target_id=context.target_id,
        )
        try:
            reply = future.result(timeout=self.operation_timeout)
        except FutureTimeout:
            # The frame was sent to SKILL. A missing response is therefore an
            # unknown outcome and must keep the generation quarantined.
            with self._mailbox_lock:
                if future.done():
                    reply = future.result()
                else:
                    self._expired.add(request_id)
                    reply = None
            if reply is None:
                if self.router:
                    self.router.mark_unknown(request_id)
                raise QueryUnavailable(
                    "skill_timeout", "Relay response deadline expired; execution status is unknown"
                ) from None
        if (
            reply.get("protocol") != VERSION
            or reply.get("kind") != "response"
            or reply.get("id") != request_id
            or reply.get("generation") != self.generation
        ):
            raise ProtocolError("Response does not match request")
        if not reply.get("ok"):
            emit_diagnostic(
                self.diagnostic,
                "router.reply",
                request_id=request_id,
                method=method,
                ok=False,
                code=reply.get("code"),
                function=(params or {}).get("function"),
                target_id=context.target_id,
                diagnostic=circuit_diagnostic(reply),
                instance_id=self.instance_id,
                generation=self.generation,
            )
            self._raise_reply_error(reply, method, context)
        result = reply.get("result")
        emit_diagnostic(
            self.diagnostic,
            "router.reply",
            request_id=request_id,
            method=method,
            ok=True,
            instance_id=self.instance_id,
            generation=self.generation,
        )
        if not isinstance(result, dict):
            raise ProtocolError("Context result must be an object")
        if method == ASSISTANT_METHOD:
            # Assistant evaluator replies use the controller's {ok,value}
            # envelope rather than a context observation's `valid` flag.
            return result
        if method in SHARED_READ_METHODS and result.get("ok") is False:
            if result.get("code") == "stale_context":
                raise QueryUnavailable(
                    "stale_context", "指定窗口已关闭或切换，请重新捕获需要读取的窗口。"
                )
            raise QueryUnavailable(
                result.get("code", "read_failed"), result.get("message", "Context unavailable")
            )
        if result.get("valid") is not True:
            if method == "get_context" and "get_project_context" in context.snapshot.get(
                "capabilities", []
            ):
                raise QueryUnavailable(
                    "stale_context",
                    "原入口窗口或视图已失效，工程绑定可通过 get_project_context 核验。",
                )
            message = (
                "工程目录已改变，请从目标工程重新打开 Silicon Copilot。"
                if method == "get_project_context" else
                "原入口窗口或视图已失效，请从目标窗口重新发起请求。"
            )
            raise NeedsReconcile(self._diagnostic(result, method, context, message))
        return result

    def _raise_reply_error(self, reply, method, context):
        code = reply.get("code")
        evidence = circuit_diagnostic(reply)
        data = {
            **evidence, "ok": False, "code": code,
            "request_id": reply.get("id"), "method": method,
            "target_id": context.target_id, "instance_id": context.instance_id,
            "generation": context.generation, "automatic_resume_allowed": False,
            **({key: reply[key] for key in ("family", "candidates") if key in reply}
               if code == "circuit_requires_target" else {}),
        }
        message = self._diagnostic(reply, method, context)
        if method == CIRCUIT_METHOD and code in {
            "simulation_project_dir_invalid", "task_source_mismatch", "circuit_preflight_failed",
            "circuit_invalid_call", "circuit_read_failed", "circuit_result_too_large",
            "circuit_invalid_reply", "circuit_requires_target",
        } and evidence.get("operation_dispatched") is False and evidence.get("category") in {
            "preflight", "validation", "execution",
        }:
            raise CircuitCallError(code, message, data=data)
        if code in {"unavailable", "read_failed", "result_too_large", "assistant_failed",
                    "request_too_large"}:
            raise QueryUnavailable(code, message)
        if code == "stale_context" and method in SHARED_READ_METHODS:
            raise QueryUnavailable(
                code, "原入口窗口已关闭或切换；工程操作可使用工程上下文并重新预检目标。"
            )
        raise NeedsReconcile(message, data=data)

    def _diagnostic(self, reply, method, context, fallback="Context request failed"):
        """Keep enough relay identity to diagnose a lost context safely."""
        detail = reply.get("error") or reply.get("message") or fallback
        if not isinstance(detail, str) or not detail.strip():
            detail = fallback
        code = reply.get("code") or "unknown"
        return (
            f"{detail} (code={code}, method={method}, target_id={context.target_id}, "
            f"instance_id={context.instance_id}, generation={context.generation})"
        )
