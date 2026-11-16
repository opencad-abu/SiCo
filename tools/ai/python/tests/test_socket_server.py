from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

from cadai.mcp import SocketClient
from cadai.protocol import decode_line, encode_line
from cadai.runtime import RuntimePaths, unix_socket_address
from cadai.socket_server import MAX_SEEN_REQUEST_IDS, UnixRequestServer


def test_authenticated_socket_routes_requests_from_multiple_clients(tmp_path: Path) -> None:
    root = tmp_path / "cad-ai-test"
    spool = root / "spool"
    spool.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    spool.chmod(0o700)
    runtime = RuntimePaths(root, root / "bridge.sock", spool)
    calls: list[tuple[str, str, dict]] = []

    def dispatch(request_id: str, method: str, params: dict):
        calls.append((request_id, method, params))
        return {"ok": True, "cwd": "/work"}

    server = UnixRequestServer(runtime.socket, "secret", dispatch)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = SocketClient(runtime.socket, "secret", 2.0, runtime)
        client.connect()

        second = SocketClient(runtime.socket, "secret", 2.0, runtime)
        second.connect()

        ok, result = client.call("get_context", {})
        second_ok, second_result = second.call("get_context", {})
        client.close()
        second.close()
        assert ok is True
        assert result == {"ok": True, "cwd": "/work"}
        assert second_ok is True
        assert second_result == {"ok": True, "cwd": "/work"}
        assert len(calls) == 2
        assert all(call[1:] == ("get_context", {}) for call in calls)
        assert runtime.socket.stat().st_mode & 0o777 == 0o600

        for index in range(MAX_SEEN_REQUEST_IDS + 1):
            assert server.claim_request_id(f"bounded-{index}") is True
        assert len(server._seen_ids) == MAX_SEEN_REQUEST_IDS
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_authenticated_client_limit_is_recoverable(tmp_path: Path) -> None:
    root = tmp_path / "cad-ai-client-limit"
    spool = root / "spool"
    spool.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    runtime = RuntimePaths(root, root / "bridge.sock", spool)
    server = UnixRequestServer(
        runtime.socket,
        "secret",
        lambda *_args: {"ok": True},
        maximum_authenticated_clients=2,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    first = SocketClient(runtime.socket, "secret", 2.0, runtime)
    second = SocketClient(runtime.socket, "secret", 2.0, runtime)
    blocked = SocketClient(runtime.socket, "secret", 2.0, runtime)
    replacement = SocketClient(runtime.socket, "secret", 2.0, runtime)
    try:
        first.connect()
        second.connect()
        assert server.wait_authenticated_clients(2, 1.0)

        ok, error = blocked.call("get_context", {})
        assert ok is False
        assert error["code"] == "bridge_in_use"
        assert server.wait_authenticated_clients(2, 1.0)

        first.close()
        assert server.wait_authenticated_clients(1, 1.0)
        replacement.connect()
        ok, result = replacement.call("get_context", {})
        assert ok is True
        assert result == {"ok": True}
    finally:
        first.close()
        second.close()
        blocked.close()
        replacement.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_multiple_clients_serialize_dispatch(tmp_path: Path) -> None:
    root = tmp_path / "cad-ai-serialized"
    spool = root / "spool"
    spool.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    runtime = RuntimePaths(root, root / "bridge.sock", spool)
    state_lock = threading.Lock()
    active = 0
    maximum_active = 0

    def dispatch(_request_id: str, _method: str, _params: dict) -> dict:
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        with state_lock:
            active -= 1
        return {"ok": True}

    server = UnixRequestServer(runtime.socket, "secret", dispatch)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    clients = [
        SocketClient(runtime.socket, "secret", 2.0, runtime),
        SocketClient(runtime.socket, "secret", 2.0, runtime),
    ]
    results: list[tuple[bool, dict]] = []
    try:
        for client in clients:
            client.connect()
        workers = [
            threading.Thread(
                target=lambda current=client: results.append(
                    current.call("get_context", {})
                )
            )
            for client in clients
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=2)

        assert len(results) == 2
        assert all(ok for ok, _result in results)
        assert maximum_active == 1
        assert all(not worker.is_alive() for worker in workers)
    finally:
        for client in clients:
            client.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_failed_authentication_does_not_consume_a_client_slot(tmp_path: Path) -> None:
    root = tmp_path / "cad-ai-auth-slot"
    spool = root / "spool"
    spool.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    runtime = RuntimePaths(root, root / "bridge.sock", spool)
    server = UnixRequestServer(
        runtime.socket,
        "secret",
        lambda *_args: {"ok": True},
        maximum_authenticated_clients=1,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    rejected = SocketClient(runtime.socket, "wrong", 2.0, runtime)
    accepted = SocketClient(runtime.socket, "secret", 2.0, runtime)
    try:
        ok, error = rejected.call("get_context", {})
        assert ok is False
        assert error["code"] == "bridge_unavailable"
        assert server.wait_authenticated_clients(0, 1.0)

        accepted.connect()
        ok, result = accepted.call("get_context", {})
        assert ok is True
        assert result == {"ok": True}
    finally:
        rejected.close()
        accepted.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_private_socket_supports_deep_cad_runtime_path(tmp_path: Path) -> None:
    root = tmp_path / ("deep-project-path-" + "x" * 90) / ".cad" / "ai" / "session"
    spool = root / "spool"
    spool.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    spool.chmod(0o700)
    runtime = RuntimePaths(root, root / "bridge.sock", spool)
    assert len(str(runtime.socket).encode()) > 107

    server = UnixRequestServer(runtime.socket, "secret", lambda *_args: {"ok": True})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = SocketClient(runtime.socket, "secret", 2.0, runtime)
        client.connect()
        ok, result = client.call("get_context", {})
        client.close()
        assert ok is True
        assert result == {"ok": True}
        assert runtime.socket.is_socket()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_quiesce_prevents_new_requests_and_waits_for_active_work(tmp_path: Path) -> None:
    root = tmp_path / "cad-ai-quiesce"
    root.mkdir(mode=0o700)
    server = UnixRequestServer(root / "bridge.sock", "secret", lambda *_args: {})
    try:
        assert server.begin_request() is True
        server.quiesce()
        assert server.begin_request() is False
        assert server.wait_idle(0) is False
        server.end_request()
        assert server.wait_idle(0) is True
    finally:
        server.server_close()


def test_unsafe_request_id_is_rejected_before_dispatch(tmp_path: Path) -> None:
    root = tmp_path / "node-local" / "cad-ai-id-check"
    shared_spool = tmp_path / "shared" / ".cad-ai-spool-id-check"
    root.mkdir(parents=True, mode=0o700)
    shared_spool.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    shared_spool.chmod(0o700)
    outside = tmp_path / "outside-sentinel"
    outside.write_text("unchanged\n", encoding="ascii")
    dispatched: list[str] = []

    def dispatch(request_id: str, _method: str, _params: dict) -> dict:
        dispatched.append(request_id)
        outside.write_text("modified\n", encoding="ascii")
        return {}

    server = UnixRequestServer(root / "bridge.sock", "secret", dispatch)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    reader = None
    try:
        with unix_socket_address(server.path) as address:
            connection.connect(address)
        reader = connection.makefile("rb")
        connection.sendall(encode_line({"type": "hello", "token": "secret"}))
        assert decode_line(reader.readline().rstrip(b"\n"))["ok"] is True

        connection.sendall(
            encode_line(
                {
                    "id": "a" * 16 + "/../../../outside-sentinel",
                    "method": "get_context",
                    "params": {},
                }
            )
        )
        response = decode_line(reader.readline().rstrip(b"\n"))

        assert response["ok"] is False
        assert response["error"]["code"] == "invalid_id"
        assert dispatched == []
        assert outside.read_text(encoding="ascii") == "unchanged\n"
        assert list(shared_spool.iterdir()) == []
    finally:
        if reader is not None:
            reader.close()
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
