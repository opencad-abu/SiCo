"""Startup distinguishes ownership, broken protocol, detached waiters and spawn failures."""

import shlex
import socket
import threading
from concurrent.futures import CancelledError
from types import SimpleNamespace

import pytest
from agent_service_launch_fixtures import controlled_spawn, reap_controlled
from agent_service_launch_fixtures import services as services
from sico.service import service_startup
from sico.service.service_discovery import Discovery
from sico.service.service_ready import BOOTSTRAP_PROTOCOL
from sico.service.service_startup import ServiceStartup, ServiceStartupError
from sico.transport.framing import Connection, ProtocolError


@pytest.mark.parametrize("state", ["other_host", "unverified"])
def test_occupied_unusable_owner_never_spawns_or_attempts_takeover(tmp_path, monkeypatch, state):
    monkeypatch.setattr(service_startup, "discover_project", lambda _project: Discovery(state))
    monkeypatch.setattr(service_startup, "spawn_service", lambda _project: pytest.fail("spawn"))
    startup = ServiceStartup(tmp_path)
    with pytest.raises(ServiceStartupError) as error:
        startup.ready.result(3)
    assert error.value.phase == "discovery" and error.value.code == state
    assert startup.wait(2)


def test_other_host_error_names_the_owner_host(tmp_path, monkeypatch):
    service = SimpleNamespace(host=SimpleNamespace(hostname="other-node"))
    monkeypatch.setattr(
        service_startup,
        "discover_project",
        lambda _project: Discovery("other_host", service=service),
    )
    monkeypatch.setattr(service_startup, "spawn_service", lambda _project: pytest.fail("spawn"))
    startup = ServiceStartup(tmp_path)
    with pytest.raises(ServiceStartupError, match="other_host") as error:
        startup.ready.result(3)
    assert "owned by host other-node" in error.value.detail
    assert "cross-host attachment is not supported" in error.value.detail
    assert (
        "sico service-stop --project-dir " + shlex.quote(str(tmp_path))
        in error.value.detail
    )
    assert "add --force if it does not respond" in error.value.detail
    assert "Do not delete owner.lock or discovery.json" in error.value.detail
    assert startup.wait(2)


@pytest.mark.parametrize("row", [
    {}, {"kind": "ready"},
    dict(protocol=BOOTSTRAP_PROTOCOL, kind=[], phase="", code=""),
    dict(protocol=BOOTSTRAP_PROTOCOL, kind="ready", phase=[], code=""),
    dict(protocol=BOOTSTRAP_PROTOCOL, kind="ready", phase="", code="", extra=True),
])
def test_invalid_bootstrap_response_remains_protocol_error(tmp_path, monkeypatch, row):
    def spawn(_project):
        parent, child = socket.socketpair()
        with child:
            Connection(child).send(row)
        return parent

    monkeypatch.setattr(service_startup, "spawn_service", spawn)
    startup = ServiceStartup(tmp_path)
    with pytest.raises(ProtocolError):
        startup.ready.result(3)
    assert startup.wait(2)


def test_ready_hint_without_live_owner_cannot_complete_startup(tmp_path, monkeypatch):
    launches = []

    def spawn(_project):
        launches.append(True)
        parent, child = socket.socketpair()
        with child:
            Connection(child).send(dict(
                protocol=BOOTSTRAP_PROTOCOL, kind="ready", phase="", code=""))
        return parent

    monkeypatch.setattr(service_startup, "spawn_service", spawn)
    startup = ServiceStartup(tmp_path)
    with pytest.raises(ServiceStartupError, match="owner_exited"):
        startup.ready.result(3)
    assert startup.wait(2) and len(launches) == 1


def test_direct_future_cancellation_detaches_from_child(tmp_path, monkeypatch):
    entered, release, processes = controlled_spawn(monkeypatch, boundary="discovery.json")
    startup, next_waiter = ServiceStartup(tmp_path), None
    try:
        assert entered.wait(5)
        assert startup.ready.cancel()
        assert startup.wait(2)
        assert processes[0].is_alive()
        with pytest.raises(CancelledError):
            startup.ready.result()
        release.set()
        next_waiter = ServiceStartup(tmp_path)
        assert next_waiter.ready.result(5).pid == processes[0].pid
    finally:
        release.set()
        startup.close()
        assert startup.wait(5)
        if next_waiter:
            next_waiter.close()
            assert next_waiter.wait(5)
        reap_controlled(processes)


def test_discovery_cancel_before_launch_has_no_process_side_effect(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def discover(_project):
        entered.set()
        assert release.wait(5)
        return Discovery("absent")

    monkeypatch.setattr(service_startup, "discover_project", discover)
    monkeypatch.setattr(service_startup, "spawn_service", lambda _project: pytest.fail("spawn"))
    startup = ServiceStartup(tmp_path)
    try:
        assert entered.wait(3)
        startup.close()
        assert startup.ready.cancelled()
    finally:
        release.set()
        assert startup.wait(3)


def test_busy_from_transient_discovery_lock_retries_only_after_lock_is_free(
    tmp_path, monkeypatch, services,
):
    real_spawn = service_startup.spawn_service
    calls = []

    def spawn(project):
        calls.append(True)
        if len(calls) > 1:
            return real_spawn(project)
        parent, child = socket.socketpair()
        with child:
            Connection(child).send(dict(
                protocol=BOOTSTRAP_PROTOCOL, kind="busy", phase="", code=""))
        return parent

    monkeypatch.setattr(service_startup, "spawn_service", spawn)
    descriptor = services(tmp_path).ready.result(10)
    assert len(calls) == 2 and descriptor.project_id
