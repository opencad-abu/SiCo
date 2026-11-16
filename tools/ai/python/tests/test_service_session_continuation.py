"""Continuation either reuses a live controller or hands off one captured host."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
import threading

import pytest

from sico.core.contracts import BoundContext
from sico.service.frontend_session import SessionView
from sico.service.session_continuation import continue_session
from sico.transport.framing import ProtocolError


CONTEXT = BoundContext("instance", "generation", "target", {})
PROJECT_ID = "a" * 32
SERVICE_ID = "b" * 32


class Host:
    def __init__(self, bridge_id="bridge", router_id="router"):
        self.descriptor = {"bridge_id": bridge_id, "router_id": router_id}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def close(self):
        self.closed = True


class Control:
    def __init__(self):
        self.guards = []
        self.calls = []

    @contextmanager
    def guard(self, address):
        self.guards.append(address)
        yield

    def continue_session(self, connection, address):
        self.calls.append((connection, address))
        return SimpleNamespace(record=lambda: {"connection": connection,
                                               "session": address.session.session_id})


class Owner:
    def __init__(self):
        self._mutation = nullcontext()
        self.controllers = {}
        self.descriptor = SimpleNamespace(project_id=PROJECT_ID, service_id=SERVICE_ID)
        self.creation = SimpleNamespace(retained=[], retain=self.retain)
        self.closed = []

    def dependencies_for(self, _key):
        return SimpleNamespace(bridge_descriptor={"bridge_id": "bridge", "router_id": "router"})

    def retain(self, controller, dependencies):
        self.creation.retained.append((controller, dependencies))

    def close_session(self, key):
        self.closed.append(key)


class Recovery:
    def __init__(self, controller):
        self.controller = controller
        self.calls = []

    def continue_session(self, connection, operation, key, **kwargs):
        self.calls.append((connection, operation, key, kwargs))
        return self.controller


class Controller:
    def __init__(self, session_id="old", *, busy=False, closing=False,
                 status="completed", inflight=()):
        self.session_id = session_id
        self.runtime_id = "c" * 32
        self.frontend_view = SessionView(
            session_id, self.runtime_id, CONTEXT, "label", "base", "model", True
        )
        self.display_name = session_id
        self.activity = lambda: "idle"
        self.closing = closing
        self._shutdown = threading.Event()
        self._lock = threading.RLock()
        self.busy = busy
        self.loop = SimpleNamespace(_inflight=inflight)
        self._task = {"status": status}
        self.continued = 0
        self.closed = 0
        self.recovery = SimpleNamespace(continue_chat=self._continue_chat)

    def _continue_chat(self, *_args):
        self.continued += 1

    def request_close(self):
        self.closed += 1


def make_hub(controller=None, recovery=None):
    owner = Owner()
    if controller is not None:
        owner.controllers[controller.session_id] = controller
    hub = SimpleNamespace(
        owner=owner,
        descriptor=SimpleNamespace(project_id=PROJECT_ID, service_id=SERVICE_ID),
        control=Control(),
        recovery=recovery or Recovery(Controller("new")),
    )
    return hub, owner


def params(session_id="old", **extra):
    return {"session_id": session_id, **extra}


def wire():
    return SimpleNamespace(connection="connection", operation="operation")


def test_continuation_rejects_malformed_and_invalid_session_envelopes():
    hub, _ = make_hub()
    with pytest.raises(ProtocolError, match="Invalid service message fields"):
        continue_session(hub, wire(), {})
    with pytest.raises(ValueError, match="invalid identifier"):
        continue_session(hub, wire(), {"session_id": "bad space"})
    with pytest.raises(ValueError, match="invalid identifier"):
        continue_session(hub, wire(), {"session_id": "old", "source_session_id": "?"})


def test_no_live_controller_opens_recovery_and_grants_control(monkeypatch):
    recovered = Controller("old")
    recovery = Recovery(recovered)
    hub, _ = make_hub(recovery=recovery)
    monkeypatch.setattr("sico.service.session_continuation.current_host",
                        lambda *_args: None)
    result = continue_session(hub, wire(), params("old", source_session_id="source"))

    assert recovery.calls == [(
        "connection", "operation", "old",
        {"preferred": "source", "selected": None},
    )]
    assert recovered.continued == 1
    assert hub.control.calls[0][0] == "connection"
    assert result["session"]["address"]["session_id"] == "old"
    assert result["control"]["session"] == "old"


def test_same_host_is_closed_before_reusing_the_live_controller(monkeypatch):
    old = Controller("old")
    hub, owner = make_hub(old)
    candidate = Host()
    monkeypatch.setattr("sico.service.session_continuation.current_host",
                        lambda *_args: (candidate,))
    result = continue_session(hub, wire(), params("old"))

    assert candidate.closed is True
    assert owner.creation.retained == []
    assert owner.closed == []
    assert old.continued == 1
    assert result["session"]["address"]["session_id"] == "old"
    assert hub.control.calls[0][1].session.session_id == "old"


def test_different_host_retires_old_controller_and_opens_selected_host(monkeypatch):
    old = Controller("old")
    new = Controller("old")
    recovery = Recovery(new)
    hub, owner = make_hub(old, recovery)
    candidate = Host("other-bridge", "other-router")
    monkeypatch.setattr("sico.service.session_continuation.current_host",
                        lambda *_args: (candidate,))
    result = continue_session(hub, wire(), params("old", source_session_id="source"))

    assert old.closed == 1
    assert owner.closed == ["old"]
    assert len(owner.creation.retained) == 1
    assert recovery.calls[0][3]["selected"] == (candidate,)
    assert new.continued == 1
    assert len(hub.control.calls) == 2
    assert result["session"]["address"]["session_id"] == "old"


def test_non_idle_controller_does_not_consider_a_new_host(monkeypatch):
    old = Controller("old", busy=True)
    hub, _ = make_hub(old)
    monkeypatch.setattr("sico.service.session_continuation.current_host",
                        lambda *_args: pytest.fail("busy session must not search a host"))
    continue_session(hub, wire(), params("old"))
    assert old.continued == 1
    assert hub.control.calls[0][1].session.session_id == "old"


def test_busy_arriving_during_handoff_aborts_before_retaining(monkeypatch):
    old = Controller("old")
    hub, owner = make_hub(old)
    candidate = Host("other-bridge", "other-router")
    monkeypatch.setattr("sico.service.session_continuation.current_host",
                        lambda *_args: (candidate,))

    original = hub.control.continue_session
    def become_busy(connection, address):
        old.busy = True
        return original(connection, address)

    hub.control.continue_session = become_busy
    with pytest.raises(ValueError, match="当前任务仍在执行"):
        continue_session(hub, wire(), params("old"))
    assert owner.creation.retained == []
    assert owner.closed == []
