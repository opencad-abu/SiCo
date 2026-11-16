"""Frontend attach/open selection and local creation without a live service."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from sico.core.contracts import BoundContext
from sico.service import frontend_attach
from sico.service.frontend_attach import FrontendAttach
from sico.transport.framing import ProtocolError

PROJECT = "a" * 32
SERVICE = "b" * 32


class Owner:
    def __init__(self, project):
        self.project = project
        self.controllers = {}
        self._mutation = nullcontext()
        self.opened = []
        self.owned = []
        self.dependencies = {}

    def dependencies_for(self, session_id):
        return self.dependencies[session_id]

    def open(self, session_id, descriptor, context, **kwargs):
        self.opened.append((session_id, descriptor, context, kwargs))
        return "controller"

    def own_resource(self, controller, callback):
        self.owned.append((controller, callback))


def controller(context=None, *, closing=False, shutdown=False):
    current = context or BoundContext("instance", "generation", "target", {})
    return SimpleNamespace(
        current=current,
        closing=closing,
        _shutdown=SimpleNamespace(is_set=lambda: shutdown),
        frontend_view=SimpleNamespace(
            session_id="new", runtime_id="c" * 32, context=current,
            label="label", base="base", model="model", resources=False),
        display_name="display",
        activity=lambda: "idle",
    )


def wire(closed=False):
    return SimpleNamespace(closed=SimpleNamespace(is_set=lambda: closed),
                           progress=None)


def dependency(**overrides):
    value = dict(bridge_descriptor={}, environment={}, provider_config=None)
    value.update(overrides)
    return SimpleNamespace(**value)


def params(**overrides):
    value = dict(session_id="new", bridge={"bridge": 1},
                 context=BoundContext("instance", "generation", "target", {}),
                 provider_config=None, environment={})
    value.update(overrides)
    return value


@pytest.fixture
def attach(tmp_path):
    return FrontendAttach(Owner(tmp_path), SimpleNamespace(
        project_id=PROJECT, service_id=SERVICE))


def test_session_record_reads_the_published_controller_view():
    value = controller()
    value.closing = True
    result = frontend_attach.session_record(
        value, SimpleNamespace(project_id=PROJECT, service_id=SERVICE))
    assert result["address"] == {
        "project_id": PROJECT, "service_id": SERVICE,
        "session_id": "new", "runtime_id": "c" * 32,
    }
    assert result["context"] == value.current
    assert result["label"] == "label" and result["display_name"] == "display"
    assert result["activity"] == "idle" and result["closing"] is True


def test_attach_rejects_unknown_missing_or_closing_sessions(attach, monkeypatch):
    with pytest.raises(ValueError, match="Unknown"):
        attach.execute(wire(), "other", {})
    with pytest.raises(ProtocolError):
        attach.execute(wire(), "attach", {})
    with pytest.raises(ValueError, match="未在当前服务"):
        attach.execute(wire(), "attach", {"session_id": "new"})
    for state in ({"closing": True}, {"shutdown": True}):
        attach.owner.controllers["new"] = controller(**state)
        with pytest.raises(ValueError, match="未在当前服务"):
            attach.execute(wire(), "attach", {"session_id": "new"})

    attach.owner.controllers["new"] = controller()
    monkeypatch.setattr(frontend_attach, "session_record",
                        lambda _controller, _descriptor: {"session": "new"})
    assert attach.execute(wire(), "attach", {"session_id": "new"}) == {
        "session": {"session": "new"}, "created": False}


def test_open_validates_context_dependencies_history_and_project(
        attach, monkeypatch):
    good = params()
    with pytest.raises(ProtocolError):
        attach.execute(wire(), "open", {"session_id": "new"})
    with pytest.raises(ValueError, match="设计目标"):
        attach.execute(wire(), "open", dict(good, context={}))
    with pytest.raises(ProtocolError, match="provider"):
        attach._dependencies(dict(good, provider_config=[]))
    with pytest.raises(ProtocolError, match="environment"):
        attach._dependencies(dict(good, environment={"BAD=KEY": "x"}))
    with pytest.raises(ProtocolError, match="environment"):
        attach._dependencies(dict(good, environment={"KEY": "\0"}))

    monkeypatch.setattr(frontend_attach, "agent_root", lambda _project: attach.owner.project)
    historical = attach.owner.project / "sessions/new"
    historical.mkdir(parents=True)
    with pytest.raises(ValueError, match="历史会话只读"):
        attach.execute(wire(), "open", good)
    historical.rmdir()
    monkeypatch.setattr(frontend_attach.os.path, "samefile", lambda *_args: False)
    with pytest.raises(ValueError, match="另一项目"):
        attach.execute(wire(), "open", good)


def test_open_reuses_matching_target_before_creating(attach, monkeypatch):
    current = params()["context"]
    existing = controller(current)
    attach.owner.controllers["old"] = existing
    attach.owner.dependencies["old"] = dependency(bridge_descriptor={"bridge": 1})
    monkeypatch.setattr(frontend_attach.os.path, "samefile", lambda *_args: True)
    monkeypatch.setattr(frontend_attach, "BridgeClient", bridge_client(current))
    monkeypatch.setattr(frontend_attach, "session_record",
                        lambda value, _descriptor: {"session": value.current.target_id})
    result = attach.execute(wire(), "open", {**params(), "session_id": "new"})
    assert result == {"session": {"session": "target"}, "created": False}
    assert not attach.owner.opened


def test_open_creates_after_bridge_validation(attach, monkeypatch):
    current = params()["context"]
    attach.owner.controllers = {}
    monkeypatch.setattr(frontend_attach.os.path, "samefile", lambda *_args: True)
    monkeypatch.setattr(frontend_attach, "BridgeClient", bridge_client(current))
    monkeypatch.setattr(frontend_attach, "session_record",
                        lambda value, _descriptor: {"session": value})
    result = attach.execute(wire(), "open", params())
    assert result["created"] is True
    assert attach.owner.opened[0][0] == "new"
    assert attach.owner.opened[0][3]["new_only"] is True
    with pytest.raises(ValueError, match="连接等待"):
        attach.owner.controllers = {}
        attach.execute(wire(True), "open", params())


def test_open_rejects_changed_bridge_context(attach, monkeypatch):
    monkeypatch.setattr(frontend_attach.os.path, "samefile", lambda *_args: True)
    other = BoundContext("other", "generation", "target", {})
    monkeypatch.setattr(frontend_attach, "BridgeClient", bridge_client(other))
    with pytest.raises(ValueError, match="不一致"):
        attach.execute(wire(), "open", params())


def bridge_client(expected):
    class Client:
        def __init__(self, descriptor):
            self.descriptor = descriptor

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def context(self, _instance, _target):
            return expected

    return Client


def test_existing_target_uses_exact_then_affinity_then_shared_anchor(attach, monkeypatch):
    current = params()["context"]
    exact = controller(current)
    attach.owner.controllers["exact"] = exact
    attach.owner.dependencies["exact"] = dependency(bridge_descriptor={"bridge": 1})
    assert attach._existing_target(params()) is exact

    attach.owner.controllers = {"affinity": controller(BoundContext("i", "g", "other", {}))}
    attach.owner.dependencies["affinity"] = dependency(
        bridge_descriptor={"bridge": 1})
    monkeypatch.setattr(frontend_attach.QuickInputRouting, "_affinity",
                        staticmethod(lambda *_args: 2))
    assert attach._existing_target(params()) is attach.owner.controllers["affinity"]

    anchor = controller(BoundContext("instance", "generation", "other", {"cwd": "/p"}))
    attach.owner.controllers = {"anchor": anchor}
    attach.owner.dependencies["anchor"] = dependency(
        bridge_descriptor={"bridge": 1})
    monkeypatch.setattr(frontend_attach.QuickInputRouting, "_affinity",
                        staticmethod(lambda *_args: 0))
    monkeypatch.setattr(frontend_attach, "design_keys", lambda _context: [])
    monkeypatch.setattr(frontend_attach, "project_key", lambda _snapshot: "/p")
    assert attach._existing_target(params()) is anchor
    assert attach._shared_anchor(anchor.current, anchor.current) is True
    assert attach._shared_anchor(
        BoundContext("instance", "generation", "other", {}),
        BoundContext("other", "generation", "other", {})) is False


def test_reusable_rejects_closing_or_shutdown_controller():
    value = controller()
    assert FrontendAttach._reusable(value) is value
    for state in ({"closing": True}, {"shutdown": True}):
        with pytest.raises(ValueError, match="正在结束"):
            FrontendAttach._reusable(controller(**state))


def test_local_parameters_and_existing_dependency_mismatch(attach, monkeypatch):
    base = dict(session_id="new", provider_config=None, environment={},
                timeout=3, context_file=None)
    for change in ({"timeout": 0}, {"timeout": float("inf")},
                   {"timeout": "3"}, {"context_file": 3}):
        with pytest.raises(ValueError, match="local connection"):
            attach.execute(wire(), "demo", {**base, **change})
    with pytest.raises(ValueError, match="等待已结束"):
        attach.execute(wire(True), "demo", base)

    attach.owner.controllers["new"] = SimpleNamespace(
        current=SimpleNamespace(instance_id="demo-instance"))
    attach.owner.dependencies["new"] = dependency()
    monkeypatch.setattr(frontend_attach, "session_record",
                        lambda value, _descriptor: {"session": value})
    assert attach.execute(wire(), "demo", base)["created"] is False
    attach.owner.dependencies["new"] = dependency(bridge_descriptor={"x": 1})
    with pytest.raises(ValueError, match="不同依赖"):
        attach.execute(wire(), "demo", base)


def test_local_demo_creates_context_and_transfers_peer(attach, monkeypatch):
    broker = FakeBroker(BoundContext("demo-instance", "g", "t", {}))
    peer = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(frontend_attach, "ContextBroker", lambda _timeout: broker)
    monkeypatch.setattr(frontend_attach, "DemoPeer", lambda *_args, **_kwargs: peer)
    monkeypatch.setattr(frontend_attach, "session_record",
                        lambda value, _descriptor: {"session": value})
    result = attach.execute(wire(), "demo", dict(
        session_id="new", provider_config=None, environment={},
        timeout=3, context_file=None))
    assert result["created"] is True
    assert attach.owner.opened[0][2].instance_id == "demo-instance"
    assert attach.owner.owned[0] == ("controller", peer.close)


def test_local_connect_waits_for_context_and_owns_cleanup(attach, monkeypatch):
    context = BoundContext("d" * 32, "d" * 32, "target", {})
    broker = FakeBroker(context)
    monkeypatch.setattr(frontend_attach, "ContextBroker", lambda _timeout: broker)
    monkeypatch.setattr(frontend_attach, "connection_instructions",
                        lambda *_args, **_kwargs: SimpleNamespace(instructions="progress"))
    monkeypatch.setattr(frontend_attach.time, "monotonic", lambda: 0)
    monkeypatch.setattr(frontend_attach.uuid, "uuid4",
                        lambda: SimpleNamespace(hex="d" * 32))
    monkeypatch.setattr(frontend_attach, "agent_root", lambda _project: attach.owner.project)
    monkeypatch.setattr("sico.storage.journal.private_dir",
                        lambda path: path.mkdir(mode=0o700))
    monkeypatch.setattr(frontend_attach, "session_record",
                        lambda value, _descriptor: {"session": value})
    result = attach.execute(wire(), "connect", dict(
        session_id="new", provider_config=None, environment={},
        timeout=3, context_file=None))
    assert result["created"] is True
    assert attach.owner.opened[0][2] == context
    assert attach.owner.owned[0][0] == "controller"


class FakeBroker:
    def __init__(self, context):
        self.context_value = context
        self.expected_identity = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def context(self, _instance, timeout=None):
        return self.context_value
