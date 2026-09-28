"""Session-owned client for the authenticated instance bridge."""

from __future__ import annotations

import os
import select
import socket
import time

from ..core.contracts import BoundContext, CircuitCallError, NeedsReconcile, identifier
from ..storage.journal import open_private
from .bridge_identity import BRIDGE_PROTOCOL, locate_path
from .bridge_tunnel import BridgeTunnel, node_name
from .broker import _CALL_CONTEXT
from .framing import Connection, ProtocolError, strict_json
from .input_gate import CAPABILITY as INPUT_GATE
from .input_gate import answer_gate
from .methods import QueryUnavailable
from .router_journal import RouterJournal


class BridgeClient:
    """ContextBroker facade; RPCs stream router progress back to the calling session."""

    def __init__(self, descriptor, *, deadline=None, cancelled=None):
        self.descriptor = dict(descriptor)
        if (
            descriptor.get("protocol") != BRIDGE_PROTOCOL
            or descriptor.get("host") != "127.0.0.1"
            or type(descriptor.get("port")) is not int
            or not 0 < descriptor["port"] < 65536
        ):
            raise ValueError("Invalid bridge endpoint")
        for key in ("instance_id", "generation", "bridge_id", "router_id", "token"):
            identifier(descriptor[key])
        self.bridge_id = descriptor["bridge_id"]
        self._sessions = set()
        self._closed = False
        self._tunnel = None
        self._lease = None
        try:
            node = node_name(descriptor.get("node", socket.gethostname()))
            if node != socket.gethostname():
                self._tunnel = BridgeTunnel(node, descriptor["port"],
                    deadline=deadline or time.monotonic() + 8, cancelled=cancelled)
            self._lease = self._connect(deadline=deadline)
            self._lease.send(dict(self.descriptor, kind="attach"))
            response = self._lease.receive(deadline=deadline or time.monotonic() + 3,
                                           cancelled=cancelled)
            self._check_error(response)
            if response.get("kind") != "attached":
                raise ProtocolError("Invalid desktop registration response")
            self.client_id = response["client_id"]
        except BaseException:
            self.close()
            raise

    @classmethod
    def discover(cls, launch_dir, context, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            try:
                path = locate_path(launch_dir, context)
                with os.fdopen(open_private(path, os.O_RDONLY), "rb") as stream:
                    descriptor = strict_json(stream.read(8193))
                break
            except FileNotFoundError:
                if time.monotonic() >= deadline:
                    raise NeedsReconcile("Instance bridge did not publish its endpoint") from None
                time.sleep(0.05)
        if (descriptor.get("instance_id"), descriptor.get("generation")) != (
            context.instance_id,
            context.generation,
        ):
            raise NeedsReconcile("Discovered bridge identity mismatch")
        return cls(descriptor)

    def _connect(self, *, deadline=None):
        timeout = 3 if deadline is None else min(3, deadline - time.monotonic())
        if timeout <= 0:
            raise TimeoutError("Instance bridge connection deadline elapsed")
        if self._tunnel is not None:
            return Connection(self._tunnel.connect(timeout))
        return Connection(
            socket.create_connection((self.descriptor["host"], self.descriptor["port"]), timeout=timeout)
        )

    @staticmethod
    def _check_error(response):
        if response.get("kind") == "error":
            message = response.get("message", "Bridge request failed")
            if response.get("error") == "QueryUnavailable":
                raise QueryUnavailable(response["code"], message)
            if response.get("error") == "CircuitCallError":
                raise CircuitCallError(response["code"], message, data=response.get("data"))
            if response.get("error") == "TimeoutError":
                raise TimeoutError(message)
            if response.get("error") == "ProtocolError":
                raise ProtocolError(message)
            if response.get("error") in {"ValueError", "KeyError", "TypeError"}:
                raise ValueError(message)
            raise NeedsReconcile(message, data=response.get("data"))

    def _call(self, operation, *, _deadline=None, _cancelled=None, **args):
        if self._closed:
            raise NeedsReconcile("Desktop bridge client is closed")
        fields = _CALL_CONTEXT.get()
        gate = fields.get("before_start") if operation in {
            "read", "circuit_call", "assistant_call"} else None
        if gate is not None:
            blocked = gate()
            if blocked is not None:
                raise QueryUnavailable(blocked.status, blocked.summary)
            if INPUT_GATE not in self.descriptor.get("capabilities", []):
                raise QueryUnavailable("waiting_user", "实例桥需要重新启动才能核对待答复依赖")
        cancelled = fields.get("cancelled")
        if (operation in {"read", "circuit_call", "assistant_call"}
                and cancelled and cancelled()):
            raise QueryUnavailable("cancelled_before_start", "任务已停止，未发送新的宿主调用")
        identity = {
            key: fields[key] for key in ("session_id", "task_id", "tool_call_id") if fields.get(key)
        }
        if "session_id" not in identity and len(self._sessions) == 1:
            identity["session_id"] = next(iter(self._sessions))
        args = {
            key: value.record() if isinstance(value, BoundContext) else value
            for key, value in args.items()
        }
        try:
            connection = self._connect(deadline=_deadline)
        except OSError as exc:
            raise NeedsReconcile(
                "Instance bridge is unavailable; inspect receipts before reconnecting"
            ) from exc
        request_id = None
        cancelled_sent = False
        try:
            connection.send(
                dict(
                    self.descriptor,
                    kind="rpc",
                    client_id=self.client_id,
                    operation=operation,
                    args=args,
                    identity=identity,
                    **{INPUT_GATE: gate is not None},
                )
            )
            connection.socket.settimeout(None)
            while True:
                cancelled = fields.get("cancelled")
                if request_id and cancelled and cancelled() and not cancelled_sent:
                    cancelled_sent = True
                    self.cancel_request(
                        BoundContext.from_record(args["context"]),
                        request_id,
                        session_id=identity["session_id"],
                    )
                if _deadline is not None or _cancelled is not None:
                    response = connection.receive(deadline=_deadline, cancelled=_cancelled)
                else:
                    if not select.select([connection.socket], [], [], 0.1)[0]:
                        if self._closed:
                            raise NeedsReconcile("Desktop bridge client closed while waiting")
                        continue
                    response = connection.receive()
                self._check_error(response)
                if response.get("kind") == INPUT_GATE:
                    answer_gate(connection, response, gate)
                elif response.get("kind") == "progress":
                    row = response["row"]
                    request_id = row["request_id"]
                    if fields.get("progress"):
                        try:
                            fields["progress"](row)
                        except Exception:
                            pass
                elif response.get("kind") == "result":
                    return response.get("result")
                else:
                    raise ProtocolError("Invalid bridge RPC response")
        except (OSError, EOFError) as exc:
            if _deadline is not None and isinstance(exc, TimeoutError):
                raise
            raise NeedsReconcile(
                "Instance bridge disconnected; query receipts before reconnecting"
            ) from exc
        finally:
            connection.close()

    def context(
        self, instance_id, target_id="bound", timeout=None, *, deadline=None, cancelled=None
    ):
        return BoundContext.from_record(
            self._call(
                "context",
                instance_id=instance_id,
                target_id=target_id,
                timeout=30 if timeout is None else timeout,
                _deadline=deadline,
                _cancelled=cancelled,
            )
        )

    def platform_environment(self, *, cancelled=None):
        if "platform_environment.v1" not in self.descriptor.get("capabilities", []):
            raise ValueError("此 CDNS-IC 实例的接入组件较旧，请更新 SiCo 集成并重启 Virtuoso")
        from ..service.background_environment import validate_environment

        return validate_environment(self._call("platform_environment",
            _deadline=time.monotonic() + 3, _cancelled=cancelled))

    def register_target(self, context):
        return self._call("register_target", context=context)

    def stage_host_payload(self, payload):
        return self._call("stage_host_payload", payload=payload)

    def host_payload(self, payload_id):
        return self._call("host_payload", payload_id=payload_id)

    def register_session(self, session_id, context):
        result = self._call("register_session", session_id=session_id, context=context)
        self._sessions.add(session_id)
        return result

    def select_session_target(self, session_id, context, *, actor="user"):
        return self._call(
            "select_session_target", session_id=session_id, context=context, actor=actor
        )

    def propose_binding(self, session_id, context, *, reason="", task_id="", origin_request_id=""):
        return self._call(
            "propose_binding",
            session_id=session_id,
            context=context,
            reason=reason,
            task_id=task_id,
            origin_request_id=origin_request_id,
        )

    def resolve_binding(self, session_id, event_id, choice):
        return self._call(
            "resolve_binding", session_id=session_id, event_id=event_id, choice=choice
        )

    def binding_snapshot(self, session_id, after=0):
        return self._call("binding_snapshot", session_id=session_id, after=after)

    def configure_bindings(self, session_id, *, auto_bind, timeout_seconds):
        return self._call(
            "configure_bindings",
            session_id=session_id,
            auto_bind=auto_bind,
            timeout_seconds=timeout_seconds,
        )

    def release_binding(self, session_id, context):
        return self._call("release_binding", session_id=session_id, context=context)

    def release_binding_resource(self, session_id, reservation_id):
        return self._call(
            "release_binding_resource", session_id=session_id, reservation_id=reservation_id
        )

    def reconcile_binding_operation(self, session_id, binding_id, request_id):
        return self._call(
            "reconcile_binding_operation",
            session_id=session_id,
            binding_id=binding_id,
            request_id=request_id,
        )

    def release_session(self, session_id):
        try:
            return self._call("release_session", session_id=session_id)
        except NeedsReconcile:
            return False
        finally:
            self._sessions.discard(session_id)

    def release_target(self, context):
        try:
            return self._call("release_target", context=context)
        except NeedsReconcile:
            return None

    def bridge_snapshot(self):
        return self._call("bridge_snapshot")

    def router_snapshot(self, context):
        try:
            return self._call("router_snapshot", context=context)
        except NeedsReconcile:
            return None

    def request_receipt(self, context, request_id, *, session_id):
        return self._call(
            "request_receipt", context=context, request_id=request_id, session_id=session_id
        )

    @classmethod
    def archived_receipt(cls, launch_dir, context, request_id, *, session_id):
        path = locate_path(launch_dir, context)
        with os.fdopen(open_private(path, os.O_RDONLY), "rb") as stream:
            descriptor = strict_json(stream.read(8193))
        identity = {
            key: descriptor[key] for key in ("instance_id", "generation", "bridge_id", "router_id")
        }
        if (identity["instance_id"], identity["generation"]) != (
            context.instance_id,
            context.generation,
        ):
            raise NeedsReconcile("Archived bridge identity mismatch")
        with RouterJournal(path.with_suffix(".state"), **identity, readonly=True) as journal:
            return journal.receipt(
                request_id, session_id=session_id, target_id=context.target_id, recovered=True
            )

    def cancel_request(self, context, request_id, *, session_id):
        return self._call(
            "cancel_request", context=context, request_id=request_id, session_id=session_id
        )

    def get_context(self, context):
        return self.read(context, "get_context")

    def read(self, context, method, params=None):
        return self._call("read", context=context, method=method, params=params)

    def circuit_call(self, context, params):
        return self._call("circuit_call", context=context, params=params)

    def assistant_call(self, context, method, code):
        return self._call("assistant_call", context=context, method=method, code=code)

    def close(self):
        self._closed = True
        if self._lease is not None:
            self._lease.close()
        if self._tunnel is not None:
            self._tunnel.close()
            self._tunnel = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
