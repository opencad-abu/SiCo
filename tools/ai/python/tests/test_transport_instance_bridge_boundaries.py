"""Bridge handshake, lease ownership and duplicate-generation boundaries."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from sico.core.contracts import BoundContext
from sico.transport.instance_bridge import InstanceBridge


class Connection:
    def __init__(self, *messages, fail_send=False):
        self.messages = iter(messages)
        self.sent = []
        self.fail_send = fail_send
        self.closed = 0
        self.socket = SimpleNamespace(settimeout=lambda _value: None)

    def receive(self):
        value = next(self.messages)
        if isinstance(value, BaseException):
            raise value
        return value

    def send(self, value):
        if self.fail_send:
            raise OSError("send failed")
        self.sent.append(value)

    def close(self):
        self.closed += 1


def bridge():
    value = InstanceBridge.__new__(InstanceBridge)
    value.descriptor = {
        "protocol": "bridge",
        "instance_id": "instance",
        "generation": "generation",
        "bridge_id": "bridge",
        "router_id": "router",
        "node": "node",
        "job": "job",
        "token": "secret",
    }
    value._changed = threading.RLock()
    value._clients = {}
    value._connections = set()
    value._workers = set()
    value.host_payloads = SimpleNamespace(release=lambda _client: None)
    value.broker = SimpleNamespace(
        release_session=lambda session: value.released.append(session),
        expected_identity=("instance", "generation"),
        bridge_snapshot=lambda: {"sessions": {}},
        binding_events=SimpleNamespace(proposals={}),
    )
    value.router = SimpleNamespace(snapshot=lambda: {"unknown_request": None})
    value._active_targets = {}
    value.targets = SimpleNamespace(records=lambda: [])
    value.target_released = None
    value.released = []
    return value


def hello(**overrides):
    value = dict(bridge().descriptor)
    value.pop("token")
    value.update(kind="attach", token="secret")
    value.update(overrides)
    return value


def test_serve_rejects_identity_and_authentication_before_lease():
    value = bridge()
    wrong = Connection(hello(instance_id="other"))
    value._serve(wrong)
    assert wrong.sent[0]["error"] == "ProtocolError"
    assert wrong.closed == 1

    wrong = Connection(hello(token="wrong"))
    value._serve(wrong)
    assert "authentication" in wrong.sent[0]["message"]


def test_serve_rejects_unknown_operation_and_reports_send_failure():
    value = bridge()
    connection = Connection(hello(kind="other"))
    value._serve(connection)
    assert connection.sent[0]["message"] == "Invalid bridge operation"

    connection = Connection(hello(instance_id="other"), fail_send=True)
    value._serve(connection)
    assert connection.closed == 1


def test_attach_lease_is_released_on_eof():
    value = bridge()
    connection = Connection(hello(), EOFError("closed"))
    value._serve(connection)
    assert connection.sent[0]["kind"] == "attached"
    assert value._clients == {}
    assert connection.closed == 1
    assert value._connections == set()


def test_rpc_requires_a_live_lease():
    value = bridge()
    connection = Connection(hello(kind="rpc", client_id="missing"))
    value._serve(connection)
    assert connection.sent[0]["message"] == "Desktop lease is closed"

    value._clients["closed"] = {
        "closed": SimpleNamespace(is_set=lambda: True),
        "sessions": {}, "targets": set()}
    connection = Connection(hello(kind="rpc", client_id="closed"))
    value._serve(connection)
    assert connection.sent[0]["message"] == "Desktop lease is closed"


def test_sync_membership_replaces_session_targets():
    value = bridge()
    value._clients["lease"] = {
        "sessions": {"session": {"old"}}, "targets": set()}
    value.broker.bridge_snapshot = lambda: {"sessions": {
        "session": {"targets": [{"target_id": "new"}]},
    }}
    value._sync_binding_membership()
    assert value._clients["lease"]["sessions"]["session"] == {"new"}


def test_duplicate_generation_is_rejected_and_first_bridge_closes(tmp_path):
    context = BoundContext("instance", "generation", "bound", {"source": "simulated"})
    first = InstanceBridge(tmp_path, context)
    try:
        with pytest.raises(RuntimeError, match="already exists"):
            InstanceBridge(tmp_path, context)
    finally:
        first.close()
        first.close()


def rpc_bridge():
    value = bridge()
    target = BoundContext("instance", "generation", "bound", {})
    value.context = target
    value.broker.context = lambda _instance, _target, timeout=30: target
    value.broker.register_target = lambda context: value.calls.append(
        ("register_target", context))
    value.broker.register_session = lambda session, context: value.calls.append(
        ("register_session", session, context))
    value.broker.select_session_target = lambda session, context, actor="user": (
        value.calls.append(("select", session, actor)) or {"selected": session})
    value.broker.release_session = lambda session: value.calls.append(
        ("release", session)) or True
    value.broker.propose_binding = lambda *args, **kwargs: {"proposal": args[0]}
    value.broker.resolve_binding = lambda session, event, choice: {
        "resolved": (session, event, choice)}
    value.broker.configure_bindings = lambda session, auto_bind, timeout_seconds: {
        "configured": (session, auto_bind, timeout_seconds)}
    value.broker.release_binding = lambda session, context: {"released": session}
    value.broker.release_binding_resource = lambda session, reservation: {
        "released_resource": (session, reservation)}
    value.broker.binding_snapshot = lambda session, after: {"after": after}
    value.broker.bridge_snapshot = lambda: {"sessions": {}}
    value.broker.router_snapshot = lambda context: {"target": context.target_id}
    value.broker.reconcile_binding_operation = lambda session, binding, request: {
        "reconciled": (session, binding, request)}
    value.calls = []
    value.targets = SimpleNamespace(
        add=lambda context: value.calls.append(("target_add", context)),
        release=lambda target_id: None, records=lambda: [])
    value.journal = SimpleNamespace(
        receipt=lambda request, session_id, target_id: {
            "request": request, "session": session_id, "target": target_id})
    value.host_payloads = SimpleNamespace(
        stage=lambda client, payload: {"staged": payload},
        read=lambda identity: {"payload": identity})
    return value, target


def test_rpc_dispatches_context_generation_and_registration():
    value, target = rpc_bridge()
    client = {"closed": threading.Event(), "sessions": {"session": set()},
              "targets": set()}
    context = target.record()
    value._rpc(Connection(), client, {"operation": "context", "args": {
        "instance_id": "instance", "target_id": "bound"}})
    value._rpc(Connection(), client, {"operation": "register_target",
                                      "args": {"context": target.record()}})
    value._rpc(Connection(), client, {"operation": "register_session",
                                      "args": {"session_id": "session",
                                               "context": context}})
    value._rpc(Connection(), client, {"operation": "select_session_target",
                                      "args": {"session_id": "session",
                                               "context": context}})
    value._rpc(Connection(), client, {"operation": "release_session",
                                      "args": {"session_id": "session",
                                               "context": context}})
    assert ("register_target", target) in value.calls
    assert ("register_session", "session", target) in value.calls
    assert ("select", "session", "user") in value.calls
    assert client["sessions"] == {}


def test_rpc_rejects_foreign_generation_and_duplicate_session():
    value, target = rpc_bridge()
    with pytest.raises(Exception, match="another bridge generation"):
        value._rpc(Connection(), {"closed": threading.Event(), "sessions": {}},
                   {"operation": "context", "args": {
                       "context": BoundContext("other", "generation", "bound", {}).record()}})
    client = {"closed": threading.Event(), "sessions": {}, "targets": set()}
    value._clients["other"] = {"closed": threading.Event(),
                               "sessions": {"session": set()}, "targets": set()}
    with pytest.raises(Exception, match="another desktop"):
        value._rpc(Connection(), client, {
            "operation": "register_session",
            "args": {"session_id": "session", "context": target.record()}})


def test_rpc_dispatches_binding_and_snapshot_operations():
    value, target = rpc_bridge()
    client = {"closed": threading.Event(), "sessions": {"session": {"bound"}},
              "targets": {"bound"}}
    args = {"session_id": "session", "context": target.record()}
    value._rpc(Connection(), client, {"operation": "propose_binding",
                                      "args": {**args, "reason": "why",
                                               "task_id": "task"}})
    value._rpc(Connection(), client, {"operation": "resolve_binding",
                                      "args": {**args, "event_id": "event",
                                               "choice": "accept"}})
    value._rpc(Connection(), client, {"operation": "configure_bindings",
                                      "args": {**args, "auto_bind": True,
                                               "timeout_seconds": 3}})
    value._rpc(Connection(), client, {"operation": "release_binding",
                                      "args": args})
    value._rpc(Connection(), client, {"operation": "release_binding_resource",
                                      "args": {**args, "reservation_id": "r"}})
    value._rpc(Connection(), client, {"operation": "binding_snapshot",
                                      "args": {**args, "after": 2}})
    value._rpc(Connection(), client, {"operation": "reconcile_binding_operation",
                                      "args": {**args, "binding_id": "b",
                                               "request_id": "r"}})
    value._rpc(Connection(), client, {"operation": "bridge_snapshot",
                                      "args": {}})
    value._rpc(Connection(), client, {"operation": "router_snapshot",
                                      "args": args})
    value._rpc(Connection(), client, {"operation": "request_receipt",
                                      "args": {**args, "request_id": "r"}})
    value._rpc(Connection(), client, {"operation": "release_target",
                                      "args": args})
    assert client["targets"] == set()
