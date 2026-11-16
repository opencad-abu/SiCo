"""Bounded handshake/control schemas never convert malformed data into authority."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from sico.service.service_handshake import (
    ServiceIncompatible,
    accept_ready,
    hello_message,
    negotiate,
)
from sico.service.service_lifecycle import ServiceLifecycle
from sico.service.service_protocol import control_claim
from sico.transport.framing import ProtocolError

DESCRIPTOR = SimpleNamespace(project_id="a" * 32, service_id="b" * 32)


@pytest.mark.parametrize("field,value", [
    ("client_id", []), ("client_id", ""), ("client_id", "x" * 100), ("nonce", True),
    ("purpose", []), ("purpose", "control"), ("protocol", None), ("protocol", "x" * 65),
    ("capabilities", {}), ("capabilities", [1]), ("capabilities", ["x"] * 17),
    ("capabilities", ["service.status.v1", "service.status.v1"]),
    ("capabilities", ["x" * 65]), ("capabilities", ["bad\nname"]),
    ("required", ["not.offered.v1"]), ("required", [None]),
])
def test_bad_hello_does_not_refresh_idle_or_create_connection(field, value):
    now = [0.0]
    lifecycle = ServiceLifecycle(idle_seconds=10, clock=lambda: now[0])
    row = hello_message(DESCRIPTOR)
    row[field] = value
    now[0] = 9
    with pytest.raises(ProtocolError):
        negotiate(row, DESCRIPTOR, lifecycle)
    assert lifecycle.status()["idle_remaining"] == 1


def test_version_rejection_is_explicit_and_inspection_handshake_does_not_renew_idle():
    now = [0.0]
    lifecycle = ServiceLifecycle(idle_seconds=10, clock=lambda: now[0])
    row = hello_message(DESCRIPTOR, purpose="inspect")
    now[0] = 9
    response, identity = negotiate(row, DESCRIPTOR, lifecycle)
    assert identity == accept_ready(response, row)
    assert lifecycle.status()["idle_remaining"] == 1
    row["protocol"] = "sico_agent_service.v999"
    response, identity = negotiate(row, DESCRIPTOR, lifecycle)
    assert identity is None
    with pytest.raises(ServiceIncompatible):
        accept_ready(response, row)


@pytest.mark.parametrize("field,value", [
    ("connection_id", ""), ("connection_id", []), ("client_id", "f" * 32),
    ("nonce", "f" * 32), ("service_id", "f" * 32), ("project_id", "f" * 32),
    ("frame_limit", True), ("frame_limit", 1024 * 1024),
    ("capabilities", []), ("capabilities", ["unknown.capability"]),
    ("capabilities", ["service.status.v1", "service.stop.v1", "service.stop.v1"]),
])
def test_bad_ready_identity_and_capabilities_are_protocol_damage(field, value):
    row = hello_message(DESCRIPTOR)
    response, _ = negotiate(row, DESCRIPTOR, ServiceLifecycle())
    response[field] = value
    with pytest.raises(ProtocolError):
        accept_ready(response, row)


@pytest.mark.parametrize("field,value", [
    ("generation", True), ("generation", 0), ("generation", -1), ("generation", 2**31),
    ("generation", "1"), ("client_id", "b" * 32), ("runtime_id", "old"),
    ("session_id", "../session"), ("session_id", "x" * 97), ("lease_id", None),
])
def test_reserved_control_claim_requires_client_session_runtime_generation_and_lease(field, value):
    row = dict(client_id="a" * 32, session_id="session", runtime_id="b" * 32,
               generation=1, lease_id="c" * 32)
    claim = control_claim(row, "a" * 32)
    assert replace(claim, generation=2).generation == 2
    row[field] = value
    with pytest.raises(ProtocolError):
        control_claim(row, "a" * 32)


@pytest.mark.parametrize("value", [None, [], {}, {"extra": "field"}])
def test_incomplete_control_claim_is_not_a_missing_optional_permission(value):
    with pytest.raises(ProtocolError):
        control_claim(value, "a" * 32)
