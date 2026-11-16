"""Socket server ownership counters, request IDs and quiescence."""

from __future__ import annotations

import socket

import pytest

from cadai.runtime import RuntimePaths
from cadai.socket_server import (
    MAX_SEEN_REQUEST_IDS,
    RequestFailure,
    UnixRequestServer,
)


def server(tmp_path, maximum=2):
    root = tmp_path / "root"
    spool = root / "spool"
    spool.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    spool.chmod(0o700)
    paths = RuntimePaths(root, root / "bridge.sock", spool)
    value = UnixRequestServer(paths.socket, "secret",
                              lambda *_args: {"ok": True},
                              maximum_authenticated_clients=maximum)
    return value, paths


def test_request_failure_exposes_a_stable_error():
    failure = RequestFailure("bad", "message", {"x": 1})
    assert failure.as_error() == {
        "code": "bad", "message": "message", "data": {"x": 1}}


def test_server_rejects_invalid_client_limit(tmp_path):
    with pytest.raises(ValueError, match="positive"):
        UnixRequestServer(tmp_path / "socket", "secret", lambda *_args: {},
                          maximum_authenticated_clients=0)


def test_request_id_claims_are_unique_and_bounded(tmp_path):
    value, paths = server(tmp_path)
    try:
        assert value.claim_request_id("one") is True
        assert value.claim_request_id("one") is False
        for index in range(MAX_SEEN_REQUEST_IDS):
            assert value.claim_request_id("id-" + str(index)) is True
        assert len(value._seen_ids) == MAX_SEEN_REQUEST_IDS
        assert value.claim_request_id("new") is True
        assert "one" not in value._seen_ids
    finally:
        value.server_close()


def test_authenticated_client_and_request_counters_are_bounded(tmp_path):
    value, paths = server(tmp_path, maximum=1)
    try:
        assert value.claim_authenticated_client() is True
        assert value.claim_authenticated_client() is False
        value.release_authenticated_client()
        assert value.wait_authenticated_clients(0, 0.1) is True
        with pytest.raises(RuntimeError, match="underflow"):
            value.release_authenticated_client()

        assert value.begin_request() is True
        assert value.wait_idle(0) is False
        value.end_request()
        assert value.wait_idle(0.1) is True
        value.quiesce()
        assert value.begin_request() is False
    finally:
        value.server_close()


def test_peer_credentials_and_socket_cleanup(tmp_path, monkeypatch):
    value, paths = server(tmp_path)
    left, right = socket.socketpair()
    try:
        assert value.peer_is_current_user(left) is True
        monkeypatch.delattr(socket, "SO_PEERCRED", raising=False)
        assert value.peer_is_current_user(left) is True
    finally:
        left.close()
        right.close()
        value.server_close()
    assert not paths.socket.exists()
    value.server_close()
