"""Authenticated instance routing and listener lifecycle, with compatibility peer/call-scope exports."""

from __future__ import annotations

from sicoenv import read as environment_setting

import os
import secrets
import socket
import threading
import uuid

from cadai.entry_context import validate_arguments

from ..core.contracts import BoundContext, NeedsReconcile, identifier
from .binding_outcomes import observe as observe_outcome
from .binding_outcomes import unconfirmed
from .bindings import BindingRegistry
from .broker_dispatch import dispatch as dispatch_native

# Compatibility imports retain the historical module entry; migrate imports to broker_peer.
from .broker_peer import Peer as Peer
from .broker_registration import authenticate, publish_peer
from .broker_routing import execute_bound, validate_request_id

# Compatibility imports retain the historical module entry; migrate imports to broker_scope.
from .broker_scope import _CALL_CONTEXT as _CALL_CONTEXT
from .broker_scope import skill_call_context as skill_call_context
from .circuit import METHOD as CIRCUIT_METHOD
from .circuit import wire_arguments as circuit_wire_arguments
from .framing import VERSION, Connection, ProtocolError
from .methods import ASSISTANT_METHOD, READ_METHODS, SHARED_READ_METHODS, QueryUnavailable
from .project import PROJECT_READ_METHODS
from .project import validate_arguments as validate_project_arguments
from .relay import (
    assistant_wire_arguments,
    diagnostic_sink_from_environment,
    emit_diagnostic,
    skill_response_timeout,
)
from .router import InstanceRegistry


class ContextBroker:
    """First increment binds loopback only. Cross-host TLS belongs to deployment work."""

    def __init__(
        self, timeout: float = 10, operation_timeout: float | None = None, diagnostic=None
    ):
        # The bridge is owned by this broker, while logical sessions are
        # registered separately below.  Keeping an explicit identity makes
        # reconnect and cross-session diagnostics unambiguous.
        self.bridge_id = uuid.uuid4().hex
        self.token = secrets.token_hex(32)
        self.timeout = timeout
        # Registration must fail quickly when Virtuoso is not connected, but a
        # dispatched SKILL operation may legitimately occupy CIW for many
        # minutes. Keep the authenticated request socket alive for that
        # operation budget instead of reusing the short registration timeout.
        self.operation_timeout = skill_response_timeout(configured=operation_timeout)
        if diagnostic is None:
            environment = dict(os.environ)
            router_path = environment_setting(environment, "SICO_AI_ROUTER_DIAGNOSTIC_LOG", "")
            if router_path:
                environment["SICO_AI_DIAGNOSTIC_LOG"] = router_path
            diagnostic = diagnostic_sink_from_environment(environment)
        self.diagnostic = diagnostic
        self.peers: dict[str, Peer] = {}
        self._registered_generations = {}
        self._changed = threading.Condition()
        self._stop = threading.Event()
        self._handshakes: set[Connection] = set()
        self.registry = InstanceRegistry(self.diagnostic)
        self.bindings = BindingRegistry()
        self._sessions = self.bindings.sessions
        from .binding_events import BindingEvents

        self.binding_events = BindingEvents(self.bindings, self._binding_live, self._binding_busy)
        from .native_bindings import NativeBindings

        self.native_bindings = NativeBindings(self)
        from .reconciliation import BindingReconciliation

        self.reconciliation = BindingReconciliation(self)
        self._session_requests = self.bindings.requests
        self._retired_peers = set()
        self.expected_identity = None
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.2)
        self.address = self.listener.getsockname()
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                sock, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            sock.settimeout(min(self.timeout, 3))
            connection = Connection(sock)
            with self._changed:
                if self._stop.is_set() or len(self._handshakes) >= 8:
                    connection.close()
                    continue
                self._handshakes.add(connection)
            threading.Thread(target=self._handshake, args=(connection,), daemon=True).start()

    def _handshake(self, connection: Connection) -> None:
        peer = None
        try:
            peer = authenticate(connection, self.token, self.diagnostic,
                                self.operation_timeout, self.expected_identity, Peer)
            with self._changed:
                if self.expected_identity and self.expected_identity != (
                    peer.instance_id, peer.generation
                ):
                    raise ProtocolError("Bridge instance identity mismatch")
                if self._stop.is_set():
                    connection.close()
                else:
                    old = self.peers.get(peer.instance_id)
                    if old and old.generation == peer.generation:
                        for target_id, context in old.contexts.items():
                            if target_id in peer.contexts and peer.contexts[target_id] != context:
                                raise ProtocolError("Reconnect changed an immutable target")
                        peer.contexts = {**old.contexts, **peer.contexts}
                    router, created = self.registry.register_with_status(
                        peer.instance_id, peer.generation
                    )
                    peer.router = router
                    # A new Virtuoso generation invalidates logical sessions
                    # bound to the previous generation.  Keep the bridge
                    # alive, but never expose stale ownership to the UI.
                    self.bindings.retire_generation(
                        peer, self.binding_events, self.diagnostic, self.bridge_id)
                    publish_peer(
                        peer, old, router, created, peers=self.peers,
                        retired_peers=self._retired_peers,
                        registered_generations=self._registered_generations,
                        diagnostic=self.diagnostic, bridge_id=self.bridge_id)
                    connection.send(
                        {"protocol": VERSION, "kind": "welcome", "generation": peer.generation}
                    )
                    connection.socket.settimeout(None)
                    peer.start()
                    self._changed.notify_all()
        except (ValueError, KeyError, TypeError, OSError, EOFError, ProtocolError):
            if peer is not None:
                self._discard_peer(peer)
            connection.close()
        finally:
            with self._changed:
                self._handshakes.discard(connection)

    def context(self, instance_id: str, target_id: str = "bound", timeout=None) -> BoundContext:
        with self._changed:
            self._changed.wait_for(
                lambda: instance_id in self.peers or self._stop.is_set(),
                self.timeout if timeout is None else timeout,
            )
            if instance_id not in self.peers:
                raise TimeoutError("Virtuoso relay did not register")
            bound = self.peers[instance_id].contexts.get(target_id)
            if bound is None:
                raise NeedsReconcile("Requested target was not registered")
            return bound

    def get_context(self, context: BoundContext) -> dict:
        return self.read(context, "get_context")

    def register_target(self, context: BoundContext) -> None:
        """Only the desktop's authenticated parent control path calls this method."""
        with self._changed:
            peer = self.peers.get(context.instance_id)
            if peer is None or peer.generation != context.generation:
                raise NeedsReconcile("Target's Virtuoso relay is unavailable")
            old = peer.contexts.get(context.target_id)
            if old and old.record() != context.record():
                raise ValueError("Target identity cannot be changed")
            if not old and len(peer.contexts) >= 64:
                raise ValueError("Too many registered targets")
            peer.contexts[context.target_id] = context

    def register_session(self, session_id: str, context: BoundContext) -> dict:
        """Add an immutable target; registration never changes the default target."""
        identifier(session_id)
        with self._changed:
            peer = self.peers.get(context.instance_id)
            if peer is None or peer.generation != context.generation:
                raise NeedsReconcile("Virtuoso 实例 bridge 不可用")
            bound = peer.contexts.get(context.target_id)
            if bound is None or bound.record() != context.record():
                raise NeedsReconcile("逻辑会话目标已失效")
            if (context.instance_id, context.generation, context.target_id) in self.native_bindings.invalidated:
                from .bindings import binding_error

                raise binding_error("binding_target_closed", "Captured window closed or changed", context)
            self._reap_sessions()
            return self.bindings.register(session_id, context, self.registry,
                                          self.bridge_id, self.diagnostic)

    def select_session_target(self, session_id, context, *, actor="user"):
        with self._changed:
            snapshot = self.binding_events.change(identifier(session_id), "select", context=context,
                                                   actor=actor)
            emit_diagnostic(self.diagnostic, "binding.default_selected", session_id=session_id,
                            target_id=context.target_id, instance_id=context.instance_id,
                            generation=context.generation)
            return snapshot

    def _binding_live(self, context):
        peer = self.peers.get(context.instance_id)
        return bool(peer and peer._failure is None and peer.generation == context.generation
                    and peer.contexts.get(context.target_id) == context
                    and (context.instance_id, context.generation, context.target_id)
                    not in self.native_bindings.invalidated)

    def _binding_busy(self, session_id, *, automatic):
        return self.bindings.busy(session_id, automatic=automatic, routers=self.registry)

    def propose_binding(self, session_id, context, **metadata):
        """Trusted host entry; only this path may attest workflow-created targets."""
        with self._changed:
            return self.binding_events.propose(session_id, context, **metadata)

    def resolve_binding(self, session_id, event_id, choice):
        with self._changed:
            return self.binding_events.resolve(session_id, event_id, choice)

    def binding_snapshot(self, session_id, after=0):
        with self._changed:
            return self.binding_events.snapshot(session_id, after)

    def poll_bindings(self):
        with self._changed:
            self.binding_events.tick()

    def configure_bindings(self, session_id, *, auto_bind, timeout_seconds):
        with self._changed:
            return self.binding_events.change(session_id, "policy", auto_bind=auto_bind,
                                              timeout_seconds=timeout_seconds)

    def release_binding(self, session_id, context):
        with self._changed:
            return self.binding_events.change(session_id, "release", context=context)

    def release_binding_resource(self, session_id, reservation_id):
        with self._changed:
            return self.binding_events.change(session_id, "release_resource", reservation_id=reservation_id)

    def reconcile_binding_operation(self, session_id, binding_id, request_id):
        return self.reconciliation.reconcile(session_id, binding_id, request_id)

    def _reap_sessions(self):
        self.bindings.reap(self.registry)

    def release_session(self, session_id: str) -> bool:
        """Detach a logical session without closing the shared bridge."""
        identifier(session_id)
        with self._changed:
            record = self._sessions.get(session_id)
            if record:
                record.releasing = True
                try:
                    self.binding_events.invalidate_session(session_id)
                except OSError:
                    record.holds["binding_journal"] = "persistence_failed"
                self._reap_sessions()
            pending = session_id in self._sessions
        if record is None:
            return False
        emit_diagnostic(
            self.diagnostic,
            "bridge.session_release_pending" if pending else "bridge.session_released",
            bridge_id=self.bridge_id,
            session_id=session_id,
            instance_id=record.anchor.instance_id,
            generation=record.anchor.generation,
        )
        return True

    def bridge_snapshot(self) -> dict:
        """Return bridge, instance and logical-session ownership state."""
        with self._changed:
            self._reap_sessions()
            peers = [
                {
                    "instance_id": instance_id,
                    "generation": peer.generation,
                    "router_id": peer.router.router_id if peer.router else None,
                }
                for instance_id, peer in self.peers.items()
            ]
            sessions = {
                session_id: record.snapshot() for session_id, record in self._sessions.items()
            }
            return {"bridge_id": self.bridge_id, "instances": peers, "sessions": sessions}

    def release_target(self, context: BoundContext) -> None:
        with self._changed:
            if any(record.targets.get(context.target_id) == context
                   for record in self._sessions.values()):
                return
            peer = self.peers.get(context.instance_id)
            if peer and peer.generation == context.generation and context.target_id != "bound":
                peer.contexts.pop(context.target_id, None)

    def read(self, context: BoundContext, method: str, params=None) -> dict:
        if method not in READ_METHODS:
            raise ValueError("Unsupported Virtuoso read method")
        if method in SHARED_READ_METHODS:
            validate_arguments(method, {} if params is None else params)
        elif method in PROJECT_READ_METHODS:
            validate_project_arguments(method, {} if params is None else params)
        elif params:
            raise ValueError("This read method takes no arguments")
        return self._request(context, method, params)

    def circuit_call(self, context: BoundContext, params: dict) -> dict:
        circuit_wire_arguments(params)
        return self._request(context, CIRCUIT_METHOD, params)

    def assistant_call(self, context: BoundContext, method: str, code: str) -> dict:
        """Run a preflighted original-Assistant operation for this source only."""
        params = {"method": method, "code": code}
        assistant_wire_arguments(params)
        return self._request(context, ASSISTANT_METHOD, params)

    def cancel_request(self, context: BoundContext, request_id: str, *, session_id: str):
        """Cancel queued work for an exact registered target and owning session."""
        identifier(session_id)
        validate_request_id(request_id)
        with self._changed:
            peer = self.peers.get(context.instance_id)
            if peer is None or peer.generation != context.generation:
                return "router_unavailable"
            bound = peer.contexts.get(context.target_id)
            if bound is None or bound.record() != context.record():
                raise NeedsReconcile("Bound context is no longer available")
        return self.registry.cancel(
            context.instance_id,
            context.generation,
            request_id,
            session_id=session_id,
            target_id=context.target_id,
        )

    def router_snapshot(self, context: BoundContext):
        """Read local routing state for an exact live target, without bridge RPC."""
        with self._changed:
            peer = self.peers.get(context.instance_id)
            if (peer is None or peer.generation != context.generation
                    or peer.contexts.get(context.target_id) != context):
                return None
            router = self.registry.get(context.instance_id, context.generation)
        if router is None:
            return None
        # Router progress callbacks may enter the broker. Never hold the
        # broker lock while waiting for the router's status publication.
        snapshot = router.snapshot()
        snapshot["bridge_id"] = self.bridge_id
        with self._changed:
            if (self.peers.get(context.instance_id) is not peer
                    or peer.contexts.get(context.target_id) != context
                    or self.registry.get(context.instance_id, context.generation) is not router):
                return None
        return dict(snapshot, bridge_id=self.bridge_id)

    def _request(self, context: BoundContext, method: str, params=None) -> dict:
        fields = _CALL_CONTEXT.get()
        cancelled = fields.get("cancelled")
        if cancelled and cancelled():
            raise QueryUnavailable("cancelled_before_start", "任务已停止，未发送新的宿主调用")
        session_id = fields.get("session_id")
        with self._changed:
            if session_id:
                self.bindings.require(session_id, context)
                self._session_requests[session_id] = self._session_requests.get(session_id, 0) + 1
        try:
            result = self._request_bound(context, method, params)
            circuit = result.get("circuit") if isinstance(result, dict) else None
            if session_id and isinstance(circuit, dict):
                with self._changed:
                    record = self._sessions.get(session_id)
                    observe_outcome(record, context, circuit,
                        lambda value: self.reconciliation.observe_run(session_id, value))
            return result
        except (NeedsReconcile, QueryUnavailable) as exc:
            if session_id and unconfirmed(method, params, exc):
                with self._changed:
                    record = self._sessions.get(session_id)
                    if record and not any(row["source"] == context.record()
                                          for row in record.operations.values()):
                        record.holds[context.target_id] = exc.code
            raise
        finally:
            if session_id:
                with self._changed:
                    remaining = self._session_requests.get(session_id, 1) - 1
                    if remaining:
                        self._session_requests[session_id] = remaining
                    else:
                        self._session_requests.pop(session_id, None)
                    self._reap_sessions()

    def _request_bound(self, context: BoundContext, method: str, params=None) -> dict:
        if method != "get_context" and method not in context.snapshot.get("capabilities", []):
            raise QueryUnavailable(
                "unavailable", "当前入口未提供此能力，请重新加载 Agent SKILL 并从目标窗口发起请求。"
            )
        with self._changed:
            peer = self.peers.get(context.instance_id)
        if peer is None or peer.generation != context.generation:
            raise NeedsReconcile(
                "Virtuoso 已断开或重启，请从当前 Virtuoso 实例重新打开 Silicon Copilot。"
            )
        with self._changed:
            router = self.registry.get(context.instance_id, context.generation)
            if router is None:
                raise NeedsReconcile(
                    "Virtuoso 路由已被替换或关闭，请从当前 Virtuoso 实例重新打开 Silicon Copilot。"
                )
        try:
            return execute_bound(
                context, method, params, call_context=_CALL_CONTEXT.get(),
                binding_lock=self._changed, bindings=self.bindings, router=router,
                request_bound=self._request_bound, dispatch=self._dispatch,
                operation_timeout=self.operation_timeout)
        except QueryUnavailable as exc:
            if exc.code == "skill_blocked_unknown":
                raise NeedsReconcile(
                    str(exc) + " (code=skill_blocked_unknown; code=skill_response_pending)"
                ) from exc
            if exc.code == "router_unavailable":
                self._discard_peer(peer)
                raise NeedsReconcile(
                    "Virtuoso 路由已关闭，连接已断开；请从当前 Virtuoso 实例重新打开 "
                    "Silicon Copilot。"
                ) from exc
            raise
        except (OSError, EOFError, ProtocolError) as exc:
            self._discard_peer(peer)
            raise NeedsReconcile(
                "Virtuoso 桥接未返回响应，连接已断开。请检查 CIW 是否有阻塞弹窗，"
                "重新连接后核对已有操作结果。"
            ) from exc

    def _dispatch(self, context, method, params, request_id, resources=None):
        return dispatch_native(
            context, method, params, request_id, resources, call_context=_CALL_CONTEXT.get(),
            admission_lock=self._changed, peers=self.peers, binding_live=self._binding_live,
            reconciliation=self.reconciliation, native_bindings=self.native_bindings,
            discard_peer=self._discard_peer)

    def _discard_peer(self, peer):
        # Retire the registry entry before a handshake can reuse it. Socket
        # teardown may finish later, when a replacement peer already exists.
        with self._changed:
            if self.peers.get(peer.instance_id) is peer:
                del self.peers[peer.instance_id]
                if self.expected_identity is None:
                    self.registry.remove(peer.instance_id, peer.generation, expected=peer.router)
        peer.close(close_router=False)

    def close(self) -> None:
        self._stop.set()
        self.listener.close()
        with self._changed:
            for peer in self.peers.values():
                peer.close()
            for peer in self._retired_peers:
                peer.close(close_router=False)
            self._retired_peers.clear()
            for connection in tuple(self._handshakes):
                connection.close()
            self.peers.clear()
            self.bindings.clear()
            self.registry.close()
            self._changed.notify_all()
        self.thread.join(timeout=1)
        if self.diagnostic is not None:
            try:
                self.diagnostic.close()
            except OSError:
                pass

    def __enter__(self) -> ContextBroker:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
