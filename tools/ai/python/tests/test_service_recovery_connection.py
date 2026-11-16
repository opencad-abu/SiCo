"""Recovery connection waits are cancellable and own the startup resources they open."""

from __future__ import annotations

from concurrent.futures import CancelledError, Future
from contextlib import ExitStack
import threading
import time
from types import SimpleNamespace

import pytest

from sico.service.recovery_connection import RecoveryConnection


class Receipt:
    def __init__(self, value=None, *, done=True):
        self.value = value
        self._done = done

    def done(self):
        return self._done

    def result(self):
        return self.value


def connection():
    value = RecoveryConnection.__new__(RecoveryConnection)
    value.ready = Future()
    value._wake = threading.Event()
    value._resources = ExitStack()
    value._resource_lock = threading.Lock()
    return value


def test_await_returns_a_completed_receipt_and_rejects_timeout_or_cancel():
    waiter = connection()
    assert waiter._await(Receipt("ready"), time.monotonic() + 1) == "ready"

    with pytest.raises(TimeoutError, match="service may still be running"):
        waiter._await(Receipt(done=False), time.monotonic() - 1)

    waiter.ready.cancel()
    with pytest.raises(CancelledError):
        waiter._await(Receipt(done=False), time.monotonic() + 1)


def test_open_keeps_startup_and_frontend_resources_until_close(monkeypatch):
    from sico.service import remote_frontend, service_startup

    events = []

    class Startup:
        def __init__(self, project):
            events.append(("startup", project))
            self.ready = Receipt({"service_id": "service"})

        def close(self):
            events.append(("startup.close",))

    class Frontend:
        def __init__(self, descriptor, project, session_id):
            events.append(("frontend", descriptor, project, session_id))
            self.recovery = SimpleNamespace(
                open_history=lambda value: Receipt(("history", value))
            )

        def close_desktop(self):
            events.append(("frontend.close",))

    monkeypatch.setattr(service_startup, "ServiceStartup", Startup)
    monkeypatch.setattr(remote_frontend, "RemoteFrontend", Frontend)
    waiter = connection()
    frontend = waiter._open("project", "session", time.monotonic() + 1)

    assert frontend == ("history", "session")
    assert events == [
        ("startup", "project"),
        ("frontend", {"service_id": "service"}, "project", "session"),
    ]
    waiter.close()
    assert events[-2:] == [("frontend.close",), ("startup.close",)]


def test_open_closes_pending_resources_when_history_attach_fails(monkeypatch):
    from sico.service import remote_frontend, service_startup

    events = []

    class Startup:
        def __init__(self, _project):
            self.ready = Receipt({"service_id": "service"})

        def close(self):
            events.append("startup.close")

    class Frontend:
        def __init__(self, *_args):
            self.recovery = SimpleNamespace(
                open_history=lambda _value: (_ for _ in ()).throw(RuntimeError("attach failed"))
            )

        def close_desktop(self):
            events.append("frontend.close")

    monkeypatch.setattr(service_startup, "ServiceStartup", Startup)
    monkeypatch.setattr(remote_frontend, "RemoteFrontend", Frontend)
    waiter = connection()
    with pytest.raises(RuntimeError, match="attach failed"):
        waiter._open("project", "session", time.monotonic() + 1)
    assert events == ["frontend.close", "startup.close"]


def test_close_cancels_ready_and_is_safe_without_resources():
    waiter = connection()
    waiter.close()
    assert waiter.ready.cancelled()
    waiter.close()
