"""Remote frontend startup distinguishes attach, create, demo and stale windows."""

from __future__ import annotations

import threading
from contextlib import ExitStack
from types import SimpleNamespace

import pytest
from sico.core.contracts import BoundContext
from sico.service import frontend_startup
from sico.service.frontend_startup import FrontendStartup
from sico.service.startup_result import StartupFailure
from sico.transport.framing import ProtocolError


class Receipt:
    def __init__(self, value):
        self.value = value
        self._done = True

    def done(self):
        return self._done

    def result(self):
        return self.value


class Service:
    def __init__(self, descriptor):
        self.ready = Receipt(descriptor)
        self.closed = False

    def close(self):
        self.closed = True


class API:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self._attach = SimpleNamespace(timeout=1)
        self.closed = False
        self.control = SimpleNamespace(request=self.control_request)
        self.recovery = SimpleNamespace(open_history=self.open_history)
        self.initial = None
        self.history = None
        self.controls = []

    def open_initial(self, operation, params, observe_creation=None):
        self.initial = (operation, params)
        if isinstance(self.error, Exception):
            raise self.error
        if observe_creation:
            observe_creation(True)
        return self.frontend

    def control_request(self, session, operation):
        self.controls.append((session, operation))
        return Receipt(True)

    def open_history(self, session):
        self.history = session
        return self.frontend

    def can_control(self, _session):
        return True

    def close_desktop(self):
        self.closed = True


def args(**overrides):
    value = dict(command="desktop-worker", launch_dir="/project", session="session",
                 provider_config=None, timeout=4, context_file=None,
                 service_id=None, runtime_id=None, message="")
    value.update(overrides)
    return SimpleNamespace(**value)


def startup(value=None, environment=None):
    item = FrontendStartup.__new__(FrontendStartup)
    item.args = value or args()
    item.environment = dict(environment or {})
    item.phase = "registration"
    item._stop = threading.Event()
    item._registration = SimpleNamespace()
    item._credential = SimpleNamespace()
    item.configuration = SimpleNamespace(set_result=lambda _value: None)
    item.registration_timeout = 30
    item.notices = []
    item.notice = lambda *args, **fields: item.notices.append((args, fields))
    return item


def install(monkeypatch, *, service_id="service", frontend=None, error=None,
            config=None):
    api = API()
    api.frontend = frontend or SimpleNamespace(
        session=SimpleNamespace(runtime_id="runtime"))
    api.error = error
    service = Service(SimpleNamespace(service_id=service_id, project_id="project"))
    monkeypatch.setattr(frontend_startup, "ServiceStartup", lambda _project: service)
    monkeypatch.setattr(frontend_startup, "RemoteFrontend", lambda *_args: api)
    item = startup()
    item._claim_window = lambda *_args: None
    item._configuration = lambda: config
    item._await = lambda receipt, progress=None: (
        receipt.result() if hasattr(receipt, "result") else receipt)
    return item, api, service


def test_await_progress_and_cancellation():
    item = startup()
    calls = []

    class Slow:
        def __init__(self):
            self.values = iter((False, True))

        def done(self):
            return next(self.values)

        def result(self):
            return "ready"

    item._check = lambda: calls.append("check")
    progress = SimpleNamespace(instructions="copy")
    assert item._await(Slow(), progress=progress) == "ready"
    assert item.instructions == "copy"
    assert calls == ["check", "check"]

    item = startup()
    item._stop.set()
    with pytest.raises(Exception):
        item._await(SimpleNamespace(done=lambda: False, result=lambda: None))


def test_configuration_waits_for_secret_and_preserves_credential_phase(monkeypatch):
    item = startup(environment={})
    item.configuration = SimpleNamespace(result=None)
    calls = []
    item.configuration = SimpleNamespace(set_result=lambda value: calls.append(value))
    item._receive = lambda channel: "secret"
    monkeypatch.setattr(frontend_startup, "resolve_provider_config",
                        lambda *_args, **_kwargs: {"api_key_env": "MODEL_KEY"})
    assert item._configuration() == {"api_key_env": "MODEL_KEY"}
    assert item.phase == "credentials"
    assert item.environment["MODEL_KEY"] == "secret"
    assert calls == ["MODEL_KEY"]

    item = startup(environment={"MODEL_KEY": "present"})
    calls = []
    item.configuration = SimpleNamespace(set_result=lambda value: calls.append(value))
    assert item._configuration() == {"api_key_env": "MODEL_KEY"}
    assert calls == [None]
    monkeypatch.setattr(frontend_startup, "resolve_provider_config",
                        lambda *_args, **_kwargs: None)
    assert item._configuration() is None


def test_prepare_creates_a_desktop_session_from_registration(monkeypatch):
    item, api, service = install(monkeypatch)
    context = BoundContext("instance", "generation", "target", {"cwd": "/project"})
    item._receive = lambda channel, timeout=None: {
        "environment": {"CAPTURED": "1"}, "context": context.record(),
        "bridge": {"instance_id": "instance", "generation": "generation",
                   "bridge_id": "bridge", "router_id": "router"},
    }
    with ExitStack() as resources:
        result = item._prepare(resources)
    assert result.frontend is api.frontend
    assert api.initial[0] == "open"
    assert api.initial[1]["context"] == context
    assert item.notices[-1][1]["bridge_id"] == "bridge"
    assert item.environment["CAPTURED"] == "1"
    assert result.attach_targets is True
    assert service.closed is True and api.closed is True


def test_prepare_service_window_attaches_without_registration(monkeypatch):
    item, api, _service = install(monkeypatch)
    item.args = args(command="service-gui", service_id="service")
    with ExitStack() as resources:
        result = item._prepare(resources)
    assert api.initial == ("attach", {"session_id": "session"})
    assert result.attach_targets is False
    assert item.notices == []


def test_prepare_service_window_falls_back_to_history(monkeypatch):
    item, api, _service = install(monkeypatch, error=ValueError("ended"))
    item.args = args(command="service-gui")
    with ExitStack() as resources:
        result = item._prepare(resources)
    assert api.history == "session"
    assert result.frontend is api.frontend


def test_prepare_rejects_stale_service_and_runtime(monkeypatch):
    item, _api, _service = install(monkeypatch, service_id="other")
    item.args = args(service_id="service")
    context = BoundContext("instance", "generation", "target", {"cwd": "/project"})
    registration = {
        "context": context.record(),
        "bridge": {"instance_id": "instance", "generation": "generation",
                   "bridge_id": "bridge", "router_id": "router"},
    }
    item._receive = lambda channel, timeout=None: registration
    with ExitStack() as resources:
        with pytest.raises(ValueError, match="原服务已结束"):
            item._prepare(resources)

    item, _api, _service = install(monkeypatch)
    item.args = args(service_id="service", runtime_id="other")
    item._receive = lambda channel, timeout=None: registration
    with ExitStack() as resources:
        with pytest.raises(ValueError, match="运行实例"):
            item._prepare(resources)


def test_prepare_demo_extends_attach_timeout_and_uses_connection_params(monkeypatch):
    item, api, _service = install(monkeypatch, config={"model": "test"})
    item.args = args(command="demo", timeout=5)
    with ExitStack() as resources:
        result = item._prepare(resources)
    assert api._attach.timeout == 35
    assert api.initial[0] == "demo"
    assert api.initial[1]["timeout"] == 5
    assert result.initial_text == ""


def test_prepare_rejects_mismatched_registration_bridge(monkeypatch):
    item, _api, _service = install(monkeypatch)
    context = BoundContext("instance", "generation", "target", {"cwd": "/project"})
    item._receive = lambda channel, timeout=None: {
        "context": context.record(),
        "bridge": {"instance_id": "other", "generation": "generation",
                   "bridge_id": "bridge", "router_id": "router"},
    }
    with ExitStack() as resources:
        with pytest.raises(ValueError, match="identity mismatch"):
            item._prepare(resources)


def test_claim_window_maps_lease_errors(monkeypatch):
    item = startup()
    resources = ExitStack()
    monkeypatch.setattr(frontend_startup, "claim_window",
                        lambda _project: (_ for _ in ()).throw(
                            frontend_startup.WindowAlreadyOpen("busy")))
    with pytest.raises(StartupFailure) as caught:
        item._claim_window(resources, "desktop-worker")
    assert caught.value.phase == "window"
    item._claim_window(resources, "demo")
    resources.close()

    closed = []
    monkeypatch.setattr(frontend_startup, "claim_window",
                        lambda _project: SimpleNamespace(close=lambda: closed.append(True)))
    resources = ExitStack()
    item._claim_window(resources, "desktop-worker")
    resources.close()
    assert closed == [True]


def test_prepare_propagates_protocol_errors_and_control_races(monkeypatch):
    item, _api, _service = install(monkeypatch, error=ProtocolError("bad"))
    item.args = args(command="service-gui")
    with ExitStack() as resources:
        with pytest.raises(ProtocolError, match="bad"):
            item._prepare(resources)

    item, api, _service = install(monkeypatch)
    item.args = args(command="service-gui")
    api.control.request = lambda *_args: (_ for _ in ()).throw(
        ProtocolError("race"))
    with ExitStack() as resources:
        with pytest.raises(ProtocolError, match="race"):
            item._prepare(resources)

    item, api, _service = install(monkeypatch)
    item.args = args(command="service-gui")
    api.control.request = lambda *_args: (_ for _ in ()).throw(
        ValueError("occupied"))
    with ExitStack() as resources:
        result = item._prepare(resources)
    assert result.frontend is api.frontend
