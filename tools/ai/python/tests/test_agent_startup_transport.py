"""Silent or partially responding peers cannot trap desktop bootstrap."""

import socket
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor

import pytest

from sico.transport.bridge_client import BridgeClient
from sico.transport.bridge_identity import BRIDGE_PROTOCOL
from sico.transport.framing import Connection, encode


@pytest.mark.parametrize("partial", [b"", b"\0\0", encode({"value": "x"})[:6]])
@pytest.mark.parametrize("stop", ["deadline", "cancel"])
def test_frame_read_cannot_wait_forever_for_header_or_body(partial, stop):
    left, right = socket.socketpair()
    connection = Connection(left)
    cancelled = threading.Event()
    try:
        if partial:
            right.sendall(partial)
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(connection.receive, deadline=time.monotonic() + 0.15,
                                  cancelled=cancelled.is_set)
            if stop == "cancel":
                cancelled.set()
            with pytest.raises(CancelledError if stop == "cancel" else TimeoutError):
                pending.result(2)
            assert pending.done()
    finally:
        connection.close()
        right.close()


@pytest.mark.parametrize("phase", ["attach", "context"])
@pytest.mark.parametrize("stop", ["deadline", "cancel"])
def test_bridge_bootstrap_stops_during_partial_reply(phase, stop, monkeypatch):
    client_socket, server_socket = socket.socketpair()
    connection, server = Connection(client_socket), Connection(server_socket)
    descriptor = {"protocol": BRIDGE_PROTOCOL, "host": "127.0.0.1", "port": 12345,
                  "instance_id": "instance", "generation": "generation", "bridge_id": "bridge",
                  "router_id": "router", "token": "token"}
    cancelled = threading.Event()
    monkeypatch.setattr(BridgeClient, "_connect", lambda self, **_: connection)
    try:
        with ThreadPoolExecutor(1) as pool:
            if phase == "attach":
                pending = pool.submit(BridgeClient, descriptor, cancelled=cancelled.is_set)
            else:
                client = object.__new__(BridgeClient)
                client.descriptor, client._sessions = descriptor, set()
                client._closed, client.client_id = False, "client"
                pending = pool.submit(client.context, "instance", "target", timeout=1,
                                      deadline=time.monotonic() + 1, cancelled=cancelled.is_set)
            server.receive()
            server_socket.sendall(b"\0\0\0\x40{")
            if stop == "cancel":
                cancelled.set()
            with pytest.raises(CancelledError if stop == "cancel" else TimeoutError):
                pending.result(4)
            assert pending.done()
            assert client_socket.fileno() == -1
            server_socket.settimeout(0.5)
            try:
                assert server_socket.recv(32) == b""
            except ConnectionResetError:
                # Cancellation closes the client side immediately; socketpair
                # peers may report reset instead of a clean EOF.
                pass
    finally:
        connection.close()
        server.close()
