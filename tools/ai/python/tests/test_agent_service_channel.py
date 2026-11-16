"""Client reply binding and frame corruption do not become accepted command results."""

import socket
import time

import pytest
from agent_service_protocol_fixtures import responder

from sico.service.service_channel import RequestUnsent, service_channel
from sico.service.service_management import exchange_service
from sico.service.service_messages import ServiceRequest
from sico.transport.framing import Connection, ProtocolError, strict_json


@pytest.mark.parametrize("field,value", [
    ("connection_id", "a" * 32), ("client_id", "b" * 32), ("service_id", "c" * 32),
    ("project_id", "d" * 32), ("request_id", 2), ("request_id", True),
    ("method", "service.stop"), ("protocol", "cad_ai_transport.v1"), ("kind", "accepted"),
    ("result", []), ("extra", True),
])
def test_bad_reply_binding_poison_connection_without_replay(tmp_path, field, value):
    def corrupt(reply):
        return dict(reply, **{field: value})

    with responder(tmp_path, corrupt) as (descriptor, listener, _):
        with service_channel(descriptor, deadline=time.monotonic() + 2) as channel:
            with pytest.raises(ProtocolError):
                channel.exchange(ServiceRequest('service.status'), deadline=time.monotonic() + 2).status.record()
            with pytest.raises(ProtocolError, match="unusable"):
                channel.exchange(ServiceRequest('service.stop'), deadline=time.monotonic() + 2).status.record()
        listener.settimeout(0.05)
        with pytest.raises(socket.timeout):
            listener.accept()


def test_malformed_stop_reply_remains_protocol_error_even_after_stop_was_accepted(tmp_path):
    def corrupt(reply):
        return dict(reply, request_id=True)

    with responder(tmp_path, corrupt) as (descriptor, _, lifecycle):
        with pytest.raises(ProtocolError):
            exchange_service(
                descriptor, ServiceRequest('service.stop'), deadline=time.monotonic() + 2
            ).status.record()
        assert lifecycle.stopping


@pytest.mark.parametrize("raw", [b"\0", b"\0\0\0", b"\0\0\0\x02", b"\0\0\0\x02{"])
def test_truncated_response_is_protocol_damage(raw):
    parent, child = socket.socketpair()
    try:
        child.sendall(raw)
        child.close()
        with pytest.raises(ProtocolError, match="Truncated"):
            Connection(parent).receive(deadline=time.monotonic() + 2)
    finally:
        parent.close()
        child.close()


def test_eof_before_any_frame_remains_transport_disconnect():
    parent, child = socket.socketpair()
    child.close()
    try:
        with pytest.raises(EOFError):
            Connection(parent).receive(deadline=time.monotonic() + 2)
    finally:
        parent.close()


def test_expired_command_is_unsent_and_connection_remains_usable(tmp_path):
    with responder(tmp_path, lambda reply: reply) as (descriptor, _listener, lifecycle):
        with service_channel(descriptor, deadline=time.monotonic() + 2) as channel:
            with pytest.raises(RequestUnsent):
                channel.exchange(ServiceRequest('service.stop'), deadline=time.monotonic() - 1).status.record()
            reply = channel.exchange(ServiceRequest('service.status'), deadline=time.monotonic() + 2).status.record()
            assert reply["state"] == "ready"
            assert not lifecycle.stopping


def test_extremely_large_idle_remaining_is_protocol_error(tmp_path):
    def corrupt(reply):
        reply["result"]["idle_remaining"] = 10**1000
        return reply

    with responder(tmp_path, corrupt) as (descriptor, _listener, _lifecycle):
        with pytest.raises(ProtocolError):
            exchange_service(
                descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
            ).status.record()


@pytest.mark.parametrize("raw", [
    '{"valid":true}'.encode("utf-16"), '{"valid":true}'.encode("utf-32"),
    b'{"value":1e999}', b'{"value":-1e999}',
])
def test_strict_json_rejects_alternate_encodings_and_numeric_overflow(raw):
    with pytest.raises(ProtocolError):
        strict_json(raw)
