# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent transport contract connection contracts."""

from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor

import pytest
from agent_transport_contract_support import context

from sico.core.contracts import BoundContext, NeedsReconcile
from sico.demo import DemoPeer
from sico.transport.broker import ContextBroker
from sico.transport.framing import VERSION, Connection, ProtocolError


def test_bad_token_does_not_block_good_registration():
    with ContextBroker(1) as broker:
        silent = socket.create_connection(broker.address)
        bad = Connection(socket.create_connection(broker.address))
        bad.send({"protocol": VERSION, "kind": "hello", "token": "incorrect"})
        with pytest.raises((EOFError, OSError)):
            bad.receive()
        peer = DemoPeer(broker)
        try:
            assert broker.get_context(broker.context(peer.context.instance_id))["valid"]
        finally:
            peer.close()
            bad.close()
            silent.close()


def test_instances_serialize_and_reject_stale_generation():
    with ContextBroker(2) as broker:
        first = DemoPeer(broker, context("i1"))
        second = DemoPeer(broker, context("i2"))
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(broker.get_context, first.context) for _ in range(12)]
                assert all(f.result()["valid"] for f in futures)
            assert broker.get_context(second.context)["valid"]
            with pytest.raises(NeedsReconcile):
                broker.get_context(context("i1", "old"))
            missing = BoundContext("i1", "g", "missing", first.context.snapshot)
            with pytest.raises(NeedsReconcile):
                broker.get_context(missing)
        finally:
            first.close()
            second.close()


def test_same_generation_reconnect_reuses_router_without_disabling_requests():
    with ContextBroker(2) as broker:
        first = DemoPeer(broker, context("reconnect", "generation"))
        original_router = broker.registry.get("reconnect", "generation")
        second = DemoPeer(broker, context("reconnect", "generation"))
        try:
            assert broker.registry.get("reconnect", "generation") is original_router
            assert original_router.snapshot()["closed"] is False
            assert broker.get_context(second.context)["valid"]
            first.close()
            assert broker.get_context(second.context)["valid"]
        finally:
            second.close()


def test_instance_bridge_registers_logical_sessions_without_new_execution_channel():
    with ContextBroker(2) as broker:
        first = DemoPeer(broker, context("bridge-a", "generation"))
        second_context = BoundContext(
            first.context.instance_id,
            first.context.generation,
            "session-b",
            dict(first.context.snapshot, window="session-b"),
        )
        broker.register_target(second_context)
        try:
            first_registration = broker.register_session("session-a", first.context)
            second_registration = broker.register_session("session-b", second_context)
            assert first_registration["bridge_id"] == second_registration["bridge_id"]
            assert first_registration["router_id"] == second_registration["router_id"]
            bridge = broker.bridge_snapshot()
            assert bridge["bridge_id"] == first_registration["bridge_id"]
            assert set(bridge["sessions"]) == {"session-a", "session-b"}
            assert len(bridge["instances"]) == 1
            assert broker.router_snapshot(first.context)["bridge_id"] == bridge["bridge_id"]
            assert broker.release_session("session-a")
            assert broker.registry.get(first.context.instance_id, first.context.generation)
            assert "session-b" in broker.bridge_snapshot()["sessions"]
        finally:
            broker.release_session("session-b")
            first.close()


def test_new_generation_replaces_connection_and_closes_old_router():
    with ContextBroker(2) as broker:
        first = DemoPeer(broker, context("reconnect", "generation-old"))
        old_router = broker.registry.get("reconnect", "generation-old")
        second = DemoPeer(broker, context("reconnect", "generation-new"))
        try:
            assert old_router.snapshot()["closed"] is True
            assert broker.registry.get("reconnect", "generation-old") is None
            assert broker.get_context(second.context)["valid"]
            with pytest.raises(NeedsReconcile):
                broker.get_context(first.context)
        finally:
            first.close()
            second.close()


def test_new_generation_retires_sessions_from_previous_generation():
    with ContextBroker(2) as broker:
        first = DemoPeer(broker, context("session-generation", "old"))
        broker.register_session("old-session", first.context)
        second = DemoPeer(broker, context("session-generation", "new"))
        try:
            bridge = broker.bridge_snapshot()
            assert "old-session" not in bridge["sessions"]
            assert bridge["instances"][0]["generation"] == "new"
        finally:
            first.close()
            second.close()


def test_malformed_reconnect_does_not_evict_healthy_generation():
    with ContextBroker(2) as broker:
        first = DemoPeer(broker, context("reconnect", "generation"))
        router = broker.registry.get("reconnect", "generation")
        malformed = Connection(socket.create_connection(broker.address, timeout=2))
        try:
            malformed.send({
                "protocol": VERSION,
                "kind": "hello",
                "token": broker.token,
                "instance_id": "reconnect",
                "generation": "generation",
                "contexts": [],
            })
            with pytest.raises((EOFError, OSError, ProtocolError)):
                malformed.receive()
            assert broker.registry.get("reconnect", "generation") is router
            assert router.snapshot()["closed"] is False
            assert broker.get_context(first.context)["valid"]
        finally:
            malformed.close()
            first.close()


def test_invalid_target_disconnect_and_identity_reconnect():
    with ContextBroker(2) as broker:
        peer = DemoPeer(broker, context(valid=False))
        with pytest.raises(NeedsReconcile, match="已失效"):
            broker.get_context(peer.context)
        peer.close()
        with pytest.raises(NeedsReconcile):
            broker.get_context(peer.context)
        restored = DemoPeer(broker, peer.context)
        try:
            assert broker.context("i").record() == restored.context.record()
        finally:
            restored.close()


def test_context_failure_preserves_relay_diagnostic_identity():
    class PeerConnection:
        def __init__(self):
            self.request_id = None

        def send(self, _message):
            self.request_id = _message["id"]

        def receive(self):
            return {
                "protocol": VERSION, "kind": "response", "id": self.request_id,
                "generation": "g", "ok": False, "code": "target_released",
            }

        def close(self):
            pass

    with ContextBroker(2) as broker:
        peer = DemoPeer(broker, context("diag"))
        original = peer.connection
        try:
            # Keep the authenticated registration but replace only the peer's
            # response channel with a deterministic code-only failure.
            registered = broker.peers["diag"]
            registered.connection = PeerConnection()
            with pytest.raises(NeedsReconcile) as raised:
                broker.get_context(peer.context)
            message = str(raised.value)
            assert "target_released" in message
            assert "method=get_context" in message
            assert "target_id=bound" in message
            assert "instance_id=diag" in message
            assert "generation=g" in message
        finally:
            registered.connection = original
            peer.close()
