"""Lifecycle command envelopes preserve protocol errors and unknown stop results."""

import socket
import time
from concurrent.futures import CancelledError
from dataclasses import replace

import pytest
from agent_service_launch_fixtures import services as services
from agent_service_protocol_fixtures import responder

from sico.service.service_allocation import Allocation
from sico.service.service_errors import RequestUnsent
from sico.service.service_handshake import (
    ServiceStopping,
    accept_ready,
    hello_message,
)
from sico.service.service_lifecycle import PendingWork, ServiceLifecycle
from sico.service.service_management import (
    StopResultUnknown,
    exchange_service,
    management_reply,
)
from sico.service.service_messages import ServiceRequest
from sico.service.service_protocol import request_message
from sico.service.service_wire import ServiceWire
from sico.transport.framing import Connection, ProtocolError


@pytest.mark.parametrize("mutation", ["state", "pending", "outcome", "reason", "allocation"])
def test_malformed_status_is_protocol_damage(tmp_path, mutation):
    def corrupt(reply):
        reply["result"][mutation] = {
            "state": [], "pending": {"jobs": True}, "outcome": "done",
            "reason": "requested", "allocation": {"secret": "key"}}[mutation]
        return reply
    with responder(tmp_path, corrupt) as (descriptor, _listener, _lifecycle):
        with pytest.raises(ProtocolError):
            exchange_service(
                descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
            ).status.record()


def test_stop_response_loss_is_unknown_and_not_automatically_retried(tmp_path):
    with responder(tmp_path, lambda _reply: None) as (descriptor, listener, lifecycle):
        with pytest.raises(StopResultUnknown):
            exchange_service(
                descriptor, ServiceRequest('service.stop'), deadline=time.monotonic() + 2
            ).status.record()
        assert lifecycle.stopping
        listener.settimeout(0.1)
        with pytest.raises(socket.timeout):
            listener.accept()


def test_pending_stop_requests_shutdown_instead_of_blocking():
    lifecycle = ServiceLifecycle(pending=lambda: PendingWork(approvals=1, reconcile=2))
    reply = management_reply("service.stop", lifecycle, Allocation())
    assert reply["outcome"] == "stop_requested" and reply["state"] == "stopping"
    assert reply["reason"] == "requested"
    assert reply["pending"]["approvals"] == 1 and reply["pending"]["reconcile"] == 2


def test_wrong_generation_stop_never_stops_current_service(tmp_path, services):
    descriptor = services(tmp_path).ready.result(10)
    with pytest.raises(RequestUnsent) as caught:
        exchange_service(
            replace(descriptor, service_id='a' * 32), ServiceRequest('service.stop'), deadline=time.monotonic() + 2
        ).status.record()
    assert isinstance(caught.value.__cause__, EOFError)
    assert exchange_service(
        descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
    ).status.record()["state"] == "ready"


@pytest.mark.parametrize("cancelled", [True, False])
def test_cancelled_or_expired_request_does_not_send_stop(tmp_path, services, cancelled):
    descriptor = services(tmp_path).ready.result(10)
    deadline = time.monotonic() + 2 if cancelled else time.monotonic() - 1
    with pytest.raises(CancelledError if cancelled else TimeoutError):
        exchange_service(
            descriptor, ServiceRequest('service.stop'), deadline=deadline, cancelled=lambda : cancelled
        ).status.record()
    assert exchange_service(
        descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
    ).status.record()["state"] == "ready"


def test_already_accepted_hello_cannot_report_ready_after_stop(tmp_path, services):
    descriptor = services(tmp_path).ready.result(10)
    with socket.socket(socket.AF_UNIX) as peer:
        peer.connect(descriptor.endpoint)
        peer.sendall(b"\0")
        exchange_service(
            descriptor, ServiceRequest('service.stop'), deadline=time.monotonic() + 2
        ).status.record()
        from sico.transport.framing import encode

        request = hello_message(descriptor)
        peer.sendall(encode(request)[1:])
        reply = Connection(peer).receive(deadline=time.monotonic() + 2)
        with pytest.raises(ServiceStopping):
            accept_ready(reply, request)


def test_invalid_stop_envelope_does_not_change_lifecycle(tmp_path, services):
    descriptor = services(tmp_path).ready.result(10)
    lifecycle = ServiceLifecycle()
    wire = ServiceWire()
    hello = hello_message(descriptor)
    identity = accept_ready(wire.reply(hello, descriptor, lifecycle, Allocation()), hello)
    for changes in ({"request_id": []}, {"protocol": "wrong"}, {"kind": []},
                    {"service_id": "a" * 32}, {"extra": True}):
        request = dict(request_message(identity, "service.stop", 1), **changes)
        with pytest.raises(ProtocolError):
            wire.reply(request, descriptor, lifecycle, Allocation())
        assert not lifecycle.stopping
