"""Host-owned instance listener, leases and RPC dispatch."""

from __future__ import annotations

import fcntl
from sicolock import lock as state_lock
import hmac
import json
import logging
import os
import secrets
import socket
import threading

from ..core.contracts import BoundContext, CircuitCallError, NeedsReconcile, identifier
from ..storage.journal import open_private, private_dir, sync_directory
from ..storage.roots import agent_root
from .binding_events import BindingJournal
from .bridge_bindings import native_binding_event, publish_binding
from .bridge_identity import BRIDGE_PROTOCOL, discovery_path
from .bridge_tunnel import node_metadata
from .broker import ContextBroker, skill_call_context
from .framing import Connection, ProtocolError
from .host_payloads import HostPayloads
from .input_gate import CAPABILITY as INPUT_GATE
from .input_gate import remote_gate
from .methods import QueryUnavailable
from .relay import diagnostic_sink_from_environment
from .router_journal import RouterJournal
from .targets import TargetRegistry


class InstanceBridge:
    """The dedicated SKILL child owns this service until its IPC pipe closes."""

    def __init__(
        self,
        launch_dir,
        context,
        *,
        operation_timeout=None,
        target_released=None,
        archive_owner=None,
    ):
        self.path = discovery_path(launch_dir, context)
        agent_root(launch_dir, create=True)
        private_dir(self.path.parent)
        self._lock_fd = open_private(self.path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR)
        try:
            state_lock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.exists():
                raise RuntimeError(
                    "Bridge generation already used; reconnect with a new generation"
                )
        except (OSError, RuntimeError) as exc:
            os.close(self._lock_fd)
            raise RuntimeError(
                "Instance bridge already exists or its generation has retired"
            ) from exc
        self.context = context
        self.target_released = target_released
        self._active_targets = {}
        self.targets = TargetRegistry(context)
        self.broker = ContextBroker(
            30,
            operation_timeout=operation_timeout,
            diagnostic=diagnostic_sink_from_environment(
                {"SICO_AI_DIAGNOSTIC_LOG": str(self.path.with_suffix(".router.jsonl"))}
            ),
        )
        self.broker.expected_identity = (context.instance_id, context.generation)
        router = self.broker.registry.register(context.instance_id, context.generation)
        self.journal = RouterJournal(
            self.path.with_suffix(".state"),
            instance_id=context.instance_id,
            generation=context.generation,
            bridge_id=self.broker.bridge_id,
            router_id=router.router_id,
        )
        router.journal = self.journal
        self.router = router
        self.binding_journal = BindingJournal(self.path.with_suffix(".bindings.jsonl"))
        self.broker.binding_events.persist = self.binding_journal.append
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(32)
        self.listener.settimeout(0.2)
        self.descriptor = dict(
            protocol=BRIDGE_PROTOCOL,
            **node_metadata(),
            host="127.0.0.1",
            port=self.listener.getsockname()[1],
            token=secrets.token_hex(32),
            instance_id=context.instance_id,
            generation=context.generation,
            bridge_id=self.broker.bridge_id,
            router_id=router.router_id,
            capabilities=[INPUT_GATE, "platform_environment.v1"],
        )
        if archive_owner:
            self.descriptor["archive_owner"] = archive_owner
        self._stopped = threading.Event()
        self._changed = threading.RLock()
        self._clients = {}
        self.host_payloads = HostPayloads()
        self._connections = set()
        self._workers = set()
        self.thread = threading.Thread(target=self._accept, name="instance-bridge", daemon=True)
        self.binding_thread = threading.Thread(
            target=self._poll_bindings, name="binding-deadlines", daemon=True
        )
        try:
            temporary = self.path.with_suffix(".part")
            with os.fdopen(
                open_private(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY), "w"
            ) as stream:
                json.dump(self.descriptor, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            sync_directory(self.path.parent)
            self.thread.start()
            self.binding_thread.start()
        except BaseException:
            self.listener.close()
            self.broker.close()
            self.binding_journal.close()
            os.close(self._lock_fd)
            raise

    def _poll_bindings(self):
        while not self._stopped.wait(0.1):
            try:
                with self._changed:
                    self.broker.poll_bindings()
                    self._sync_binding_membership()
            except (OSError, ValueError):
                logging.getLogger(__name__).exception("Binding deadline persistence failed")
                return

    def _sync_binding_membership(self):
        sessions = self.broker.bridge_snapshot()["sessions"]
        for client in self._clients.values():
            for session_id in client["sessions"]:
                record = sessions.get(session_id)
                if record:
                    client["sessions"][session_id] = {
                        target["target_id"] for target in record["targets"]
                    }

    def publish_binding(self, session_id, context, **metadata):
        return publish_binding(self, session_id, context, **metadata)

    def native_binding_event(self, message):
        return native_binding_event(self, message)

    def _accept(self):
        while not self._stopped.is_set():
            try:
                sock, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            sock.settimeout(3)
            connection = Connection(sock)
            with self._changed:
                if len(self._connections) >= 128 or self._stopped.is_set():
                    connection.close()
                    continue
                self._connections.add(connection)
                worker = threading.Thread(target=self._serve, args=(connection,), daemon=True)
                self._workers.add(worker)
                worker.start()

    def _serve(self, connection):
        lease_id = None
        try:
            hello = connection.receive()
            if any(
                hello.get(key) != self.descriptor[key]
                for key in ("protocol", "instance_id", "generation", "bridge_id", "router_id", "node", "job")
            ):
                raise ProtocolError("Bridge identity mismatch")
            token = hello.get("token")
            if not isinstance(token, str) or not hmac.compare_digest(
                token, self.descriptor["token"]
            ):
                raise ProtocolError("Bridge authentication failed")
            if hello.get("kind") == "attach":
                lease_id = secrets.token_hex(32)
                with self._changed:
                    self._clients[lease_id] = {
                        "id": lease_id,
                        "sessions": {},
                        "targets": set(),
                        "closed": threading.Event(),
                    }
                connection.send(dict(kind="attached", client_id=lease_id))
                connection.socket.settimeout(None)
                connection.receive()  # EOF releases this client's sessions, never the router.
            elif hello.get("kind") == "rpc":
                with self._changed:
                    client = self._clients.get(hello.get("client_id"))
                if client is None or client["closed"].is_set():
                    raise ProtocolError("Desktop lease is closed")
                self._rpc(connection, client, hello)
            else:
                raise ProtocolError("Invalid bridge operation")
        except (
            ValueError,
            KeyError,
            TypeError,
            OSError,
            EOFError,
            NeedsReconcile,
            CircuitCallError,
            QueryUnavailable,
        ) as exc:
            try:
                connection.send(
                    dict(
                        kind="error",
                        error=type(exc).__name__,
                        message=str(exc),
                        code=getattr(exc, "code", None),
                        data=getattr(exc, "data", None),
                    )
                )
            except (OSError, ValueError):
                pass
        finally:
            with self._changed:
                if lease_id:
                    client = self._clients.pop(lease_id, None)
                    if client:
                        client["closed"].set()
                        self.host_payloads.release(client)
                        for session_id in client["sessions"]:
                            self.broker.release_session(session_id)
                        self._release_unused_targets()
                self._connections.discard(connection)
                self._workers.discard(threading.current_thread())
        connection.close()

    def _release_unused_targets(self):
        retained = set(self._active_targets)
        snapshot = self.router.snapshot()
        if snapshot["unknown_request"]:
            retained.add(snapshot["unknown_request"].get("target_id"))
        for client in self._clients.values():
            retained.update(client["targets"])
            for targets in client["sessions"].values():
                retained.update(targets)
        for session in self.broker.bridge_snapshot()["sessions"].values():
            retained.update(target["target_id"] for target in session["targets"])
        retained.update(
            proposal["target_id"]
            for proposal in self.broker.binding_events.proposals.values()
            if proposal["status"] == "proposed"
        )
        for record in self.targets.records():
            context = BoundContext.from_record(record)
            if context.target_id == self.context.target_id or context.target_id in retained:
                continue
            self.broker.release_target(context)
            self.targets.release(context.target_id)
            if self.target_released:
                self.target_released(context.target_id)

    def _rpc(self, connection, client, message):
        operation, args = message["operation"], message.get("args", {})
        context = BoundContext.from_record(args["context"]) if "context" in args else None
        if context and (context.instance_id, context.generation) != self.broker.expected_identity:
            raise NeedsReconcile("Desktop target belongs to another bridge generation")
        if operation == "platform_environment":
            from ..service.background_environment import capture_environment

            result = capture_environment()
        elif operation == "stage_host_payload":
            result = self.host_payloads.stage(client, args["payload"])
        elif operation == "host_payload":
            result = self.host_payloads.read(args["payload_id"])
        elif operation == "context":
            if args["instance_id"] != self.context.instance_id:
                raise NeedsReconcile("Bridge instance mismatch")
            result = self.broker.context(
                args["instance_id"], args["target_id"], timeout=min(args.get("timeout", 30), 30)
            ).record()
        elif operation == "register_target":
            self.broker.context(context.instance_id, self.context.target_id)
            with self._changed:
                if client["closed"].is_set():
                    raise NeedsReconcile("Desktop lease is closed")
                self.targets.add(context)
                self.broker.register_target(context)
                client["targets"].add(context.target_id)
            result = None
        elif operation in {"register_session", "release_session", "select_session_target"}:
            session_id = identifier(args["session_id"])
            with self._changed:
                if client["closed"].is_set():
                    raise NeedsReconcile("Desktop lease is closed")
                if operation == "register_session":
                    if any(
                        session_id in other["sessions"] and other is not client
                        for other in self._clients.values()
                    ):
                        raise NeedsReconcile("Session already belongs to another desktop")
                    result = self.broker.register_session(session_id, context)
                    client["sessions"].setdefault(session_id, set()).add(context.target_id)
                elif operation == "select_session_target":
                    if context.target_id not in client["sessions"].get(session_id, set()):
                        raise NeedsReconcile("Target is not registered to this desktop session")
                    result = self.broker.select_session_target(
                        session_id, context, actor=args.get("actor", "user")
                    )
                else:
                    result = session_id in client["sessions"] and self.broker.release_session(
                        session_id
                    )
                    client["sessions"].pop(session_id, None)
                    self._release_unused_targets()
        elif operation in {
            "propose_binding",
            "resolve_binding",
            "binding_snapshot",
            "configure_bindings",
            "release_binding",
            "release_binding_resource",
        }:
            session_id = identifier(args["session_id"])
            with self._changed:
                if client["closed"].is_set() or session_id not in client["sessions"]:
                    raise NeedsReconcile("Binding session does not belong to this desktop")
                if operation == "propose_binding":
                    if context.target_id not in client["targets"]:
                        raise ValueError("Binding candidate was not registered by this desktop")
                    result = self.broker.propose_binding(
                        session_id,
                        context,
                        reason=args.get("reason", ""),
                        task_id=args.get("task_id", ""),
                        origin_request_id=args.get("origin_request_id", ""),
                    )
                elif operation == "resolve_binding":
                    result = self.broker.resolve_binding(
                        session_id, args["event_id"], args["choice"]
                    )
                elif operation == "configure_bindings":
                    result = self.broker.configure_bindings(
                        session_id,
                        auto_bind=args["auto_bind"],
                        timeout_seconds=args["timeout_seconds"],
                    )
                elif operation == "release_binding":
                    result = self.broker.release_binding(session_id, context)
                elif operation == "release_binding_resource":
                    result = self.broker.release_binding_resource(
                        session_id, args["reservation_id"]
                    )
                else:
                    result = self.broker.binding_snapshot(session_id, args.get("after", 0))
                self._sync_binding_membership()
        elif operation == "reconcile_binding_operation":
            session_id = identifier(args["session_id"])
            with self._changed:
                if client["closed"].is_set() or session_id not in client["sessions"]:
                    raise NeedsReconcile("Reconciliation session does not belong to this desktop")
            result = self.broker.reconcile_binding_operation(
                session_id, identifier(args["binding_id"]), args["request_id"]
            )
        elif operation == "bridge_snapshot":
            result = self.broker.bridge_snapshot()
        elif operation == "router_snapshot":
            result = self.broker.router_snapshot(context)
        elif operation == "request_receipt":
            result = self.journal.receipt(
                args["request_id"], session_id=args["session_id"], target_id=context.target_id
            )
        elif operation == "release_target":
            with self._changed:
                client["targets"].discard(context.target_id)
                self._release_unused_targets()
            result = None
        elif operation == "cancel_request":
            if args["session_id"] not in client["sessions"]:
                result = "not_owner"
            else:
                result = self.broker.cancel_request(
                    context, args["request_id"], session_id=args["session_id"]
                )
        elif operation in {"read", "assistant_call", "circuit_call"}:
            identity = message.get("identity", {})
            if identity.get("session_id") not in client["sessions"]:
                raise NeedsReconcile("Request session is not registered to this desktop")
            if context.target_id not in client["sessions"][identity["session_id"]]:
                from .bindings import binding_error

                raise binding_error(
                    "binding_target_not_owned",
                    "Target is not bound to this desktop session",
                    context,
                )
            fields = {
                key: identifier(identity[key])
                for key in ("session_id", "task_id", "tool_call_id")
                if identity.get(key)
            }
            with self._changed:
                self._active_targets[context.target_id] = (
                    self._active_targets.get(context.target_id, 0) + 1
                )
            try:

                def progress(row):
                    connection.send(
                        dict(
                            kind="progress",
                            row=dict(
                                row,
                                instance_id=self.context.instance_id,
                                generation=self.context.generation,
                                bridge_id=self.broker.bridge_id,
                            ),
                        )
                    )

                with skill_call_context(
                    **fields, cancelled=client["closed"].is_set, progress=progress,
                    before_start=(remote_gate(connection, client["closed"])
                                  if message.get(INPUT_GATE) is True else None),
                ):
                    if operation == "read":
                        result = self.broker.read(context, args["method"], args.get("params"))
                    elif operation == "assistant_call":
                        result = self.broker.assistant_call(context, args["method"], args["code"])
                    else:
                        result = self.broker.circuit_call(context, args["params"])
            finally:
                with self._changed:
                    self._active_targets[context.target_id] -= 1
                    if not self._active_targets[context.target_id]:
                        self._active_targets.pop(context.target_id)
                    self._release_unused_targets()
        else:
            raise ProtocolError("Unsupported bridge RPC")
        connection.send(dict(kind="result", result=result))

    def close(self):
        if self._stopped.is_set():
            return
        self._stopped.set()
        self.listener.close()
        with self._changed:
            for client in self._clients.values():
                client["closed"].set()
            for connection in tuple(self._connections):
                connection.close()
            workers = tuple(self._workers)
        self.broker.close()
        self.host_payloads.release_all()
        self.thread.join(timeout=1)
        self.binding_thread.join(timeout=1)
        for worker in workers:
            worker.join(timeout=1)
        self.journal.close()
        self.binding_journal.close()
        # Do not seal while a native request or a local writer can still append.
        if (
            not self.router.skill_pending()
            and not self.thread.is_alive()
            and not self.binding_thread.is_alive()
            and not self.broker.thread.is_alive()
            and not any(worker.is_alive() for worker in workers)
        ):
            from .router_archive import seal_generation

            try:
                seal_generation(self.path, self.journal.identity)
            except (OSError, ValueError):
                logging.getLogger(__name__).exception(
                    "Bridge logs could not be sealed for archival"
                )
        # Keep the discovery tombstone: the same generation may not start again.
        os.close(self._lock_fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
