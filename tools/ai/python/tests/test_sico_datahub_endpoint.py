"""Explicit Unix endpoint lifecycle, authentication and dispatch contracts."""

import socket

import pytest
from sico_datahub.endpoint import UnixEnvelopeClient, UnixEnvelopeServer
from sico_datahub.envelope import Envelope, Meta
from sico_datahub.errors import HubError


def _site_handler(request):
    return Envelope(
        contract=request.contract,
        version=request.version,
        action=request.action,
        id=request.id,
        payload={
            "schema": "site.capability.v1",
            "scope": request.payload["scope"],
            "read_only": True,
        },
        meta=Meta(source="python", target="python"),
    )


def _request(request_id="r1", scope="endpoint"):
    return Envelope(
        contract="sico.site.capability.v1",
        version=1,
        action="probe",
        id=request_id,
        payload={"scope": scope},
        meta=Meta(source="python", target="python"),
    )


def _server(tmp_path):
    return UnixEnvelopeServer(
        tmp_path / "datahub.sock",
        "token-1",
        "session-1",
        {("sico.site.capability.v1", "probe"): _site_handler},
    )


def test_endpoint_authenticates_and_dispatches(tmp_path):
    server = _server(tmp_path)
    server.start()
    try:
        with UnixEnvelopeClient(
            server.path, "token-1", "session-1"
        ) as client:
            response = client.exchange(_request())
            assert response.status == "ok"
            assert response.payload == {
                "schema": "site.capability.v1",
                "scope": "endpoint",
                "read_only": True,
            }
            assert response.id == "r1"
        assert server.capabilities == ("site.python",)
        assert server._thread.is_alive()
    finally:
        server.close()
    assert not server.path.exists()
    assert not server._thread.is_alive()


def test_endpoint_rejects_wrong_token_and_closes_generation(tmp_path):
    with _server(tmp_path) as server:
        with pytest.raises(HubError) as failure:
            UnixEnvelopeClient(server.path, "wrong", "session-1").connect()
        assert failure.value.code == "capability_not_available"
        with UnixEnvelopeClient(server.path, "token-1", "session-1") as client:
            assert client.exchange(_request()).status == "ok"


def test_endpoint_rejects_unknown_action_as_envelope(tmp_path):
    with _server(tmp_path) as server:
        with UnixEnvelopeClient(server.path, "token-1", "session-1") as client:
            response = client.exchange(Envelope(
                contract="sico.cad.objects.v1",
                version=1,
                action="list",
                id="objects-1",
                payload={
                    "window_ref": "wctx_fixture_1",
                    "kind": "all",
                    "query": "",
                    "offset": 0,
                    "limit": 50,
                },
            ))
            assert response.status == "error"
            assert response.error.code == "capability_not_available"


def test_endpoint_maps_handler_failure_without_leaking_exception(tmp_path):
    def failing(_request):
        raise RuntimeError("private failure detail")

    server = UnixEnvelopeServer(
        tmp_path / "datahub.sock",
        "token-1",
        "session-1",
        {("sico.site.capability.v1", "probe"): failing},
    )
    with server:
        with UnixEnvelopeClient(server.path, "token-1", "session-1") as client:
            response = client.exchange(_request())
            assert response.error.code == "internal"
            assert response.error.message == "Endpoint handler failed"


def test_endpoint_bad_frame_closes_connection_without_response(tmp_path):
    with _server(tmp_path) as server:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(server.path))
            client.sendall(b"not-a-datahub-frame\n")
            assert client.recv(1) == b""


def test_endpoint_client_requires_hello_before_exchange(tmp_path):
    with _server(tmp_path) as server:
        client = UnixEnvelopeClient(server.path, "token-1", "session-1")
        with pytest.raises(HubError, match="not connected"):
            client.exchange(_request())
        client.close()
