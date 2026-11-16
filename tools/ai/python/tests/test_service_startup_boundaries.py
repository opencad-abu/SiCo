"""Service startup waits without replacing a busy or foreign owner."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sico.service import service_startup
from sico.service.service_handshake import ServiceStopping
from sico.service.service_startup import ServiceStartup, ServiceStartupError
from sico.transport.framing import ProtocolError


def startup():
    value = ServiceStartup.__new__(ServiceStartup)
    value.ready = SimpleNamespace(cancelled=lambda: False)
    value._cancelled = lambda: False
    value._wake = threading.Event()
    value._check = lambda _deadline: None
    return value


def descriptor(service_id="service", hostname="host"):
    return SimpleNamespace(service_id=service_id, host=SimpleNamespace(hostname=hostname))


def test_constructor_requires_an_absolute_project(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        ServiceStartup("relative")
    with pytest.raises(ValueError, match="absolute"):
        ServiceStartup("")


def test_startup_error_has_phase_code_and_safe_host_detail(tmp_path):
    error = ServiceStartupError("bootstrap", "owner_exited")
    assert error.phase == "bootstrap" and error.code == "owner_exited"
    assert "owner_exited" in str(error)
    detail = service_startup._other_host_detail("other", tmp_path)
    assert "cross-host" in detail and "owner.lock" in detail


def test_bootstrap_accepts_ready_and_rejects_bad_or_failed_rows(monkeypatch):
    class Channel:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    @contextmanager
    def spawn(_project):
        yield Channel()

    monkeypatch.setattr(service_startup, "spawn_service", spawn)
    rows = iter((
        {"protocol": service_startup.BOOTSTRAP_PROTOCOL, "kind": "ready",
         "phase": "start", "code": ""},
        {"protocol": service_startup.BOOTSTRAP_PROTOCOL, "kind": "failed",
         "phase": "bootstrap", "code": "owner_exited"},
        {"bad": "row"},
    ))
    monkeypatch.setattr(service_startup, "Connection",
                        lambda *_args, **_kwargs: SimpleNamespace(
                            receive=lambda **_kwargs: next(rows)))
    value = startup()
    assert value._bootstrap("project", time.monotonic() + 1) == "ready"
    with pytest.raises(ServiceStartupError, match="owner_exited"):
        value._bootstrap("project", time.monotonic() + 1)
    with pytest.raises(ProtocolError):
        value._bootstrap("project", time.monotonic() + 1)


def test_bootstrap_disconnect_is_explicit(monkeypatch):
    @contextmanager
    def spawn(_project):
        yield object()

    monkeypatch.setattr(service_startup, "spawn_service", spawn)
    monkeypatch.setattr(service_startup, "Connection",
                        lambda *_args, **_kwargs: SimpleNamespace(
                            receive=lambda **_kwargs: (_ for _ in ()).throw(
                                EOFError("closed"))))
    with pytest.raises(ServiceStartupError) as caught:
        startup()._bootstrap("project", time.monotonic() + 1)
    assert caught.value.phase == "bootstrap"


def test_prepare_returns_a_live_candidate(monkeypatch):
    value = startup()
    found = SimpleNamespace(state="local_candidate", service=descriptor())
    monkeypatch.setattr(service_startup, "discover_project", lambda _project: found)
    monkeypatch.setattr(service_startup, "probe_ready",
                        lambda *_args, **_kwargs: "descriptor")
    assert value._prepare("project", time.monotonic() + 1) == "descriptor"


def test_prepare_bootstraps_absent_and_waits_for_local_candidate(monkeypatch):
    value = startup()
    found = iter((
        SimpleNamespace(state="absent", service=None),
        SimpleNamespace(state="local_candidate", service=descriptor()),
    ))
    monkeypatch.setattr(service_startup, "discover_project", lambda _project: next(found))
    monkeypatch.setattr(value, "_bootstrap", lambda *_args: "ready")
    monkeypatch.setattr(service_startup, "probe_ready",
                        lambda *_args, **_kwargs: "descriptor")
    assert value._prepare("project", time.monotonic() + 1) == "descriptor"


def test_prepare_handles_stopping_and_owner_exit(monkeypatch):
    value = startup()
    found = iter((
        SimpleNamespace(state="local_candidate", service=descriptor()),
        SimpleNamespace(state="absent", service=None),
        SimpleNamespace(state="local_candidate", service=descriptor()),
    ))
    calls = {"probe": 0}
    monkeypatch.setattr(service_startup, "discover_project", lambda _project: next(found))
    def probe(*_args, **_kwargs):
        calls["probe"] += 1
        if calls["probe"] == 1:
            raise ServiceStopping("stopping")
        return "descriptor"
    monkeypatch.setattr(service_startup, "probe_ready", probe)
    monkeypatch.setattr(value, "_bootstrap", lambda *_args: "busy")
    assert value._prepare("project", time.monotonic() + 1) == "descriptor"

    value = startup()
    found = iter((
        SimpleNamespace(state="absent", service=None),
        SimpleNamespace(state="absent", service=None),
    ))
    monkeypatch.setattr(service_startup, "discover_project", lambda _project: next(found))
    monkeypatch.setattr(value, "_bootstrap", lambda *_args: "ready")
    with pytest.raises(ServiceStartupError, match="owner_exited"):
        value._prepare("project", time.monotonic() + 1)


def test_prepare_rejects_foreign_or_unverified_owner(monkeypatch):
    for state in ("other_host", "unverified"):
        value = startup()
        monkeypatch.setattr(service_startup, "discover_project",
                            lambda _project, state=state: SimpleNamespace(
                                state=state, service=descriptor()))
        with pytest.raises(ServiceStartupError) as caught:
            value._prepare("project", time.monotonic() + 1)
        assert caught.value.code == state
