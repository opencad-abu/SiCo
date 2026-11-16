"""Peer limits, slow readers, heartbeat expiry and listener shutdown are isolated."""

import selectors
import socket
import threading
import time
from contextlib import ExitStack

import pytest
from agent_service_listener_fixtures import WriteGate, listener_service
from agent_service_protocol_fixtures import raw_channel

from sico.service import service_listener as listener_module
from sico.service.service_allocation import Allocation
from sico.service.service_channel import service_channel
from sico.service.service_lifecycle import ServiceLifecycle
from sico.service.service_management import exchange_service
from sico.service.service_messages import ServiceRequest
from sico.service.service_protocol import FRAME_LIMIT, heartbeat_message, request_message
from sico.transport.framing import Connection, encode


def test_connection_cap_counts_clients_and_releases_one_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(listener_module, "MAX_CLIENTS", 2)
    with listener_service(tmp_path) as server, ExitStack() as stack:
        first, _ = stack.enter_context(raw_channel(server.descriptor))
        second, binding = stack.enter_context(raw_channel(server.descriptor))
        with socket.socket(socket.AF_UNIX) as excess:
            excess.settimeout(1)
            excess.connect(server.descriptor.endpoint)
            assert excess.recv(1) == b""
        first.close()
        second.send(heartbeat_message(binding))
        assert second.receive(deadline=time.monotonic() + 1)["kind"] == "heartbeat_ack"
        assert exchange_service(
            server.descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 1
        ).status.record()["state"] == "ready"


def test_slow_output_keeps_selector_writable_then_resumes_exact_frame():
    reader, writer = socket.socketpair()
    writer.setblocking(False)
    gate = WriteGate(writer)
    frame = encode({"kind": "test", "content": "x" * 100})
    peer = listener_module._Peer(time.monotonic() + 3, output=frame)
    with reader, writer, selectors.DefaultSelector() as selector:
        selector.register(gate, selectors.EVENT_WRITE, peer)
        listener_module._exchange(selector, selector.get_key(gate), None,
                                  ServiceLifecycle(), Allocation())
        assert gate.entered.is_set() and peer.output == frame
        assert selector.get_key(gate).events == selectors.EVENT_WRITE
        gate.release.set()
        listener_module._exchange(selector, selector.get_key(gate), None,
                                  ServiceLifecycle(), Allocation())
        assert not peer.output and selector.get_key(gate).events == selectors.EVENT_READ
        assert Connection(reader).receive(deadline=time.monotonic() + 1)["kind"] == "test"


def test_slow_reader_does_not_block_healthy_client_and_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(listener_module, "HEARTBEAT_GRACE", 2)
    original = listener_module._exchange
    original_drop = listener_module._drop
    saturated = []
    installed, dropped = threading.Event(), threading.Event()
    target = []

    def exchange(selector, key, *args):
        if (target and key.data.output and key.data.wire._identity.connection_id == target[0]
                and not saturated):
            sock = key.fileobj
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            while True:
                try:
                    sock.send(b"x" * 4096)
                except BlockingIOError:
                    break
            saturated.append(sock)
            installed.set()
        return original(selector, key, *args)

    def drop(selector, sock):
        original_drop(selector, sock)
        if saturated and sock is saturated[0]:
            dropped.set()

    monkeypatch.setattr(listener_module, "_exchange", exchange)
    monkeypatch.setattr(listener_module, "_drop", drop)
    with listener_service(tmp_path) as server:
        with raw_channel(server.descriptor) as (slow, binding):
            target.append(binding.connection_id)
            slow.send(request_message(binding, "service.status", 1))
            assert installed.wait(1)
            assert exchange_service(
                server.descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 1
            ).status.record()[
                "state"] == "ready"
            assert not dropped.is_set()
            assert dropped.wait(3)
            slow.socket.settimeout(1)
            drained = bytearray()
            while True:
                data = slow.socket.recv(4096)
                if not data:
                    break
                drained.extend(data)
            assert drained and set(drained) == {ord("x")}
        assert exchange_service(
            server.descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 1
        ).status.record()["state"] == "ready"


def test_output_overflow_drops_only_offending_peer(tmp_path, monkeypatch):
    monkeypatch.setattr(listener_module, "MAX_OUTPUT", FRAME_LIMIT)
    with listener_service(tmp_path) as server:
        with raw_channel(server.descriptor) as (connection, binding):
            frames = b"".join(encode(request_message(binding, "service.status", index))
                              for index in range(1, 9))
            assert len(frames) <= FRAME_LIMIT + 4
            connection.socket.sendall(frames)
            with pytest.raises(EOFError):
                connection.receive(deadline=time.monotonic() + 1)
        assert exchange_service(
            server.descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 1
        ).status.record()["state"] == "ready"


def test_heartbeat_refreshes_connection_then_idle_peer_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(listener_module, "HEARTBEAT_GRACE", 0.3)
    with listener_service(tmp_path) as server:
        with service_channel(server.descriptor, deadline=time.monotonic() + 1) as channel:
            for _ in range(4):
                time.sleep(0.12)
                assert channel.heartbeat(deadline=time.monotonic() + 1)
            assert channel.exchange(ServiceRequest('service.status'), deadline=time.monotonic() + 1).status.record()[
                "state"] == "ready"
            with pytest.raises(EOFError):
                channel._connection.receive(deadline=time.monotonic() + 1)
        assert exchange_service(
            server.descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 1
        ).status.record()["state"] == "ready"


def test_partial_authenticated_frame_cannot_renew_heartbeat_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(listener_module, "HEARTBEAT_GRACE", 0.2)
    with listener_service(tmp_path) as server:
        with raw_channel(server.descriptor) as (connection, _binding):
            connection.socket.sendall(b"\0")
            assert exchange_service(
                server.descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 1
            ).status.record()[
                "state"] == "ready"
            with pytest.raises(EOFError):
                connection.receive(deadline=time.monotonic() + 1)


def test_stopping_caps_authenticated_idle_cleanup_deadline(tmp_path):
    with listener_service(tmp_path) as server:
        with raw_channel(server.descriptor):
            assert exchange_service(
                server.descriptor, ServiceRequest('service.stop'), deadline=time.monotonic() + 1
            ).status.record()[
                "outcome"] == "stop_requested"
            server.thread.join(2)
            assert not server.thread.is_alive()


@pytest.mark.parametrize("field", ["connection_id", "service_id", "client_id"])
def test_bad_heartbeat_identity_drops_only_that_connection(tmp_path, field):
    with listener_service(tmp_path) as server:
        with raw_channel(server.descriptor) as (connection, binding):
            connection.send(dict(heartbeat_message(binding), **{field: "f" * 32}))
            with pytest.raises(EOFError):
                connection.receive(deadline=time.monotonic() + 1)
        assert exchange_service(
            server.descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 1
        ).status.record()["state"] == "ready"
