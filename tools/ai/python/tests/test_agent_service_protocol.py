"""Real service sockets enforce negotiation, generations and request envelopes."""

import socket
import struct
import time

import pytest
from agent_service_launch_fixtures import services as services
from agent_service_protocol_fixtures import raw_channel

from sico.service.service_channel import service_channel
from sico.service.service_handshake import (
    ServiceIncompatible,
    accept_ready,
    hello_message,
)
from sico.service.service_management import exchange_service
from sico.service.service_messages import ServiceRequest
from sico.service.service_peer import service_connection
from sico.service.service_protocol import CAPABILITIES, FRAME_LIMIT, request_message
from sico.transport.framing import encode


def test_same_client_gets_distinct_connection_identity_and_ordered_results(tmp_path, services):
    descriptor = services(tmp_path).ready.result(10)
    with raw_channel(descriptor, client_id="a" * 32) as (first, old):
        with raw_channel(descriptor, client_id="a" * 32) as (second, new):
            assert old.connection_id != new.connection_id and old.client_id == new.client_id
            first.send(request_message(old, "service.status", 1))
            second.send(request_message(new, "service.status", 1))
            old_reply = first.receive(deadline=time.monotonic() + 2)
            new_reply = second.receive(deadline=time.monotonic() + 2)
            assert old_reply["connection_id"] == old.connection_id
            assert new_reply["connection_id"] == new.connection_id
            # A reconnect cannot accept a captured old connection's command.
            second.send(request_message(old, "service.stop", 2))
            with pytest.raises(EOFError):
                second.receive(deadline=time.monotonic() + 2)
    assert exchange_service(
        descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
    ).status.record()["state"] == "ready"


def test_fragmented_hello_and_coalesced_requests_preserve_sequence(tmp_path, services):
    descriptor = services(tmp_path).ready.result(10)
    with service_connection(descriptor, deadline=time.monotonic() + 2) as connection:
        hello = hello_message(descriptor)
        frame = encode(hello)
        for byte in frame:
            connection.socket.sendall(bytes([byte]))
        identity = accept_ready(connection.receive(deadline=time.monotonic() + 2), hello)
        first = encode(request_message(identity, "service.status", 1))
        second = encode(request_message(identity, "service.status", 2))
        connection.socket.sendall(first + second[:7])
        assert connection.receive(deadline=time.monotonic() + 2)["request_id"] == 1
        third = encode(request_message(identity, "service.status", 3))
        connection.socket.sendall(second[7:] + third)
        replies = [connection.receive(deadline=time.monotonic() + 2) for _ in range(2)]
        assert [reply["request_id"] for reply in replies] == [2, 3]
        assert all(reply["result"]["state"] == "ready" for reply in replies)


@pytest.mark.parametrize("changes", [
    {"request_id": True}, {"request_id": 0}, {"request_id": 2}, {"request_id": "1"},
    {"client_id": "b" * 32}, {"connection_id": "b" * 32}, {"service_id": "b" * 32},
    {"project_id": "b" * 32}, {"protocol": "cad_ai_transport.v1"}, {"method": []},
    {"method": "session.submit"}, {"control": {}}, {"extra": True}, {"kind": "hello"},
])
def test_invalid_request_is_disconnected_before_stop_dispatch(tmp_path, services, changes):
    descriptor = services(tmp_path).ready.result(10)
    with raw_channel(descriptor) as (connection, identity):
        row = dict(request_message(identity, "service.stop", 1), **changes)
        connection.send(row)
        with pytest.raises(EOFError):
            connection.receive(deadline=time.monotonic() + 2)
    assert exchange_service(
        descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
    ).status.record()["state"] == "ready"


def test_missing_control_field_and_repeated_request_cannot_execute_stop(tmp_path, services):
    descriptor = services(tmp_path).ready.result(10)
    for missing in (True, False):
        with raw_channel(descriptor) as (connection, identity):
            row = request_message(identity, "service.stop", 1)
            if missing:
                del row["control"]
            else:
                connection.send(request_message(identity, "service.status", 1))
                connection.receive(deadline=time.monotonic() + 2)
            connection.send(row)
            with pytest.raises(EOFError):
                connection.receive(deadline=time.monotonic() + 2)
    assert exchange_service(
        descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
    ).status.record()["state"] == "ready"


def test_unnegotiated_capability_and_valid_control_claim_grant_no_authority(tmp_path, services):
    descriptor = services(tmp_path).ready.result(10)
    with raw_channel(descriptor, capabilities=["service.status.v1"],
                     required=["service.status.v1"]) as (connection, identity):
        row = request_message(identity, "service.status", 1)
        connection.send(dict(row, method="service.stop"))
        with pytest.raises(EOFError):
            connection.receive(deadline=time.monotonic() + 2)
    with raw_channel(descriptor) as (connection, identity):
        row = request_message(identity, "service.stop", 1)
        row["control"] = dict(client_id=identity.client_id, session_id="session",
                              runtime_id="c" * 32, generation=1, lease_id="d" * 32)
        connection.send(row)
        with pytest.raises(EOFError):
            connection.receive(deadline=time.monotonic() + 2)
    assert exchange_service(
        descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
    ).status.record()["state"] == "ready"


def test_optional_capability_is_ignored_and_required_unknown_capability_rejected(
    tmp_path, services,
):
    descriptor = services(tmp_path).ready.result(10)
    offered = [*CAPABILITIES, "future.events.v1"]
    with service_channel(descriptor, deadline=time.monotonic() + 2,
                         capabilities=offered) as channel:
        assert channel.identity.capabilities == CAPABILITIES
        assert channel.exchange(ServiceRequest('service.status'), deadline=time.monotonic() + 2).status.record()["state"] == "ready"
    with pytest.raises(ServiceIncompatible, match="missing_capability"):
        with service_channel(descriptor, deadline=time.monotonic() + 2,
                             capabilities=offered, required=offered):
            pytest.fail("Incompatible connection was accepted")


@pytest.mark.parametrize("raw", [
    b'{"kind":"hello","kind":"hello"}', b'{"nonce":NaN}', b'[]', b'{"nonce":"\xff"}',
    b'{"nonce":"\\ud800"}', b'{"a":' + b'[' * 1100 + b']' * 1100 + b'}',
    b'{"value":1e999}', '{"kind":"hello"}'.encode("utf-16"),
])
def test_invalid_json_is_isolated_to_its_connection(tmp_path, services, raw):
    descriptor = services(tmp_path).ready.result(10)
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(2)
        sock.connect(descriptor.endpoint)
        sock.sendall(struct.pack("!I", len(raw)) + raw)
        assert sock.recv(1) == b""
    assert exchange_service(
        descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
    ).status.record()["state"] == "ready"


@pytest.mark.parametrize("after_hello", [False, True])
def test_frame_limit_applies_before_and_after_negotiation(tmp_path, services, after_hello):
    descriptor = services(tmp_path).ready.result(10)
    with service_connection(descriptor, deadline=time.monotonic() + 2) as connection:
        if after_hello:
            hello = hello_message(descriptor)
            connection.send(hello)
            accept_ready(connection.receive(deadline=time.monotonic() + 2), hello)
        connection.socket.sendall(struct.pack("!I", FRAME_LIMIT + 1))
        assert connection.socket.recv(1) == b""
    assert exchange_service(
        descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
    ).status.record()["state"] == "ready"


def test_unhandshaken_status_and_old_one_shot_protocol_cannot_bypass_negotiation(
    tmp_path, services,
):
    descriptor = services(tmp_path).ready.result(10)
    with raw_channel(descriptor) as (_connection, identity):
        request = request_message(identity, "service.stop", 1)
    for row in (request, dict(protocol="sico_project_service.v1", kind="stop",
                             project_id=descriptor.project_id, service_id=descriptor.service_id,
                             nonce="a" * 32)):
        with service_connection(descriptor, deadline=time.monotonic() + 2) as connection:
            connection.send(row)
            with pytest.raises(EOFError):
                connection.receive(deadline=time.monotonic() + 2)
    assert exchange_service(
        descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
    ).status.record()["state"] == "ready"


def test_second_hello_cannot_reset_a_live_connection_sequence(tmp_path, services):
    descriptor = services(tmp_path).ready.result(10)
    with raw_channel(descriptor) as (connection, identity):
        connection.send(request_message(identity, "service.status", 1))
        connection.receive(deadline=time.monotonic() + 2)
        connection.send(hello_message(descriptor))
        with pytest.raises(EOFError):
            connection.receive(deadline=time.monotonic() + 2)
    assert exchange_service(
        descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
    ).status.record()["state"] == "ready"


@pytest.mark.parametrize("raw", [b"\0", b"\0\0\0\x10{", b"\0\0\0\0"])
def test_truncated_or_zero_length_client_frame_does_not_affect_other_connections(
    tmp_path, services, raw,
):
    descriptor = services(tmp_path).ready.result(10)
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(2)
        sock.connect(descriptor.endpoint)
        sock.sendall(raw)
        sock.shutdown(socket.SHUT_WR)
        assert sock.recv(1) == b""
    assert exchange_service(
        descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
    ).status.record()["state"] == "ready"
