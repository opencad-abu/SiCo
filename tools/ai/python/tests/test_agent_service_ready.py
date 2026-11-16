"""Readiness rejects stale identities and malformed peers without blocking other clients."""

import os
import socket
import struct
import threading
import time
from dataclasses import replace

import pytest
from agent_service_launch_fixtures import services as services

from sico.service import service_peer
from sico.service.project_service import ProjectServiceLease
from sico.service.service_errors import RequestUnsent
from sico.service.service_handshake import ServiceIncompatible, accept_ready, hello_message
from sico.service.service_listener import MAX_CLIENTS
from sico.service.service_peer import peer_identity
from sico.service.service_protocol import FRAME_LIMIT
from sico.service.service_ready import probe_ready
from sico.service.service_runtime import listening_socket
from sico.transport.framing import Connection, ProtocolError


def test_wrong_project_generation_and_process_identity_cannot_be_ready(tmp_path, services):
    descriptor = services(tmp_path).ready.result(10)
    for changed in (replace(descriptor, project_id="a" * 32),
                    replace(descriptor, service_id="b" * 32)):
        with pytest.raises(RequestUnsent) as caught:
            probe_ready(changed, deadline=time.monotonic() + 2)
        assert isinstance(caught.value.__cause__, EOFError)
    for changed in (replace(descriptor, pid=os.getpid()),
                    replace(descriptor, process_start="0")):
        with pytest.raises(ProtocolError, match="identity mismatch"):
            probe_ready(changed, deadline=time.monotonic() + 2)


def test_peer_credentials_reject_other_uid():
    class Peer:
        def getsockopt(self, *_args):
            return struct.pack("3i", os.getpid(), os.getuid() + 1, os.getgid())

    with pytest.raises(ProtocolError, match="peer identity"):
        peer_identity(Peer())


@pytest.mark.parametrize("response", [
    b'{"kind":"ready","kind":"ready"}', b'[]',
    b'{"protocol":"wrong","kind":"ready"}',
    b'{"protocol":"sico_project_service.v1","kind":"ready"}',
    b"{invalid json}", b'x' * (FRAME_LIMIT + 1),
])
def test_malformed_or_incomplete_ready_response_is_a_protocol_error(tmp_path, response):
    failures = []
    with ProjectServiceLease.acquire(tmp_path) as lease, listening_socket() as (listener, endpoint):
        descriptor = lease.publish(endpoint)

        def respond():
            try:
                with listener.accept()[0] as sock:
                    Connection(sock).receive(deadline=time.monotonic() + 2)
                    sock.sendall(struct.pack("!I", len(response)) + response)
            except Exception as exc:
                failures.append(exc)
        thread = threading.Thread(target=respond)
        thread.start()
        try:
            with pytest.raises(ProtocolError):
                probe_ready(descriptor, deadline=time.monotonic() + 2)
        finally:
            thread.join(3)
        assert not thread.is_alive() and not failures


def test_nonce_mismatch_is_rejected(tmp_path):
    with ProjectServiceLease.acquire(tmp_path) as lease, listening_socket() as (listener, endpoint):
        descriptor = lease.publish(endpoint)

        def respond():
            with listener.accept()[0] as sock:
                connection = Connection(sock)
                row = connection.receive(deadline=time.monotonic() + 2)
                connection.send(dict(row, kind="ready", nonce="c" * 32))
        thread = threading.Thread(target=respond)
        thread.start()
        try:
            with pytest.raises(ProtocolError):
                probe_ready(descriptor, deadline=time.monotonic() + 2)
        finally:
            thread.join(3)
        assert not thread.is_alive()


@pytest.mark.parametrize("raw", [
    struct.pack("!I", FRAME_LIMIT + 1),
    struct.pack("!I", 2) + b"[]",
    struct.pack("!I", 13) + b'{"kind":null}',
])
def test_bad_clients_do_not_break_listener(tmp_path, services, raw):
    descriptor = services(tmp_path).ready.result(10)
    with socket.socket(socket.AF_UNIX) as peer:
        peer.settimeout(2)
        peer.connect(descriptor.endpoint)
        peer.sendall(raw)
        assert peer.recv(1) == b""
    assert probe_ready(descriptor, deadline=time.monotonic() + 2) == descriptor


def test_partial_frame_does_not_block_other_clients(tmp_path, services):
    descriptor = services(tmp_path).ready.result(10)
    with socket.socket(socket.AF_UNIX) as slow:
        slow.connect(descriptor.endpoint)
        slow.sendall(b"\0")
        assert probe_ready(descriptor, deadline=time.monotonic() + 0.5) == descriptor
        slow.settimeout(2)
        assert slow.recv(1) == b""


def test_connection_slots_expire_and_listener_recovers(tmp_path, services):
    descriptor = services(tmp_path).ready.result(10)
    sockets = []
    try:
        for _ in range(MAX_CLIENTS + 2):
            peer = socket.socket(socket.AF_UNIX)
            sockets.append(peer)
            peer.settimeout(3)
            peer.connect(descriptor.endpoint)
        for peer in sockets:
            assert peer.recv(1) == b""
        assert probe_ready(descriptor, deadline=time.monotonic() + 2) == descriptor
    finally:
        for peer in sockets:
            peer.close()


def test_readiness_does_not_accept_another_protocol(tmp_path, services):
    descriptor = services(tmp_path).ready.result(10)
    with socket.socket(socket.AF_UNIX) as peer:
        peer.connect(descriptor.endpoint)
        row = hello_message(descriptor)
        row["protocol"] += ".future"
        connection = Connection(peer)
        connection.send(row)
        reply = connection.receive(deadline=time.monotonic() + 2)
        with pytest.raises(ServiceIncompatible, match="protocol_mismatch"):
            accept_ready(reply, row)


def test_client_rejects_boot_change_before_connect(tmp_path, services, monkeypatch):
    descriptor = services(tmp_path).ready.result(10)
    monkeypatch.setattr(service_peer, "local_host", lambda: replace(
        descriptor.host, boot_id="00000000-0000-0000-0000-000000000000"))
    with pytest.raises(ProtocolError, match="host identity"):
        probe_ready(descriptor, deadline=time.monotonic() + 2)
