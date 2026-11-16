"""Project session ownership, isolation and construction rollback."""

from types import SimpleNamespace

import pytest
from sico.core.contracts import BoundContext
from sico.service.project_sessions import ProjectSessionOwner
from sico.storage.journal import SessionJournal


def context(target):
    return BoundContext("instance", "generation", target, {"cwd": "/project"})


class Bridge:
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.closed = False
        self.sessions = []

    def register_session(self, session_id, _context):
        self.sessions.append(session_id)

    def close(self):
        self.closed = True


def test_owner_captures_independent_inputs_and_one_registry(tmp_path, monkeypatch):
    bridges, providers = [], []

    def make_bridge(descriptor):
        bridge = Bridge(descriptor)
        bridges.append(bridge)
        return bridge

    def make_provider(config, environment):
        providers.append((config, environment))
        return SimpleNamespace(label=config["model"], base="test", model=config["model"])

    monkeypatch.setattr("sico.service.project_sessions.provider_from_config", make_provider)
    first_config = {"model": "first"}
    first_environment = {"SICO_API_KEY": "first-secret", "EDA_MODE": "first"}
    first_descriptor = {"endpoint": "first", "token": "private-first"}
    owner = ProjectSessionOwner(tmp_path, provider_config=first_config,
                                environment=first_environment, bridge_factory=make_bridge)
    try:
        first = owner.open("a" * 32, first_descriptor, context("first"))
        second = owner.open("b" * 32, {"endpoint": "second", "token": "private-second"},
                            context("second"), provider_config={"model": "second"},
                            environment={"SICO_API_KEY": "second-secret", "EDA_MODE": "second"})
        assert owner.controllers == {first.session_id: first, second.session_id: second}
        assert not hasattr(owner, "sessions")
        assert [call[0]["model"] for call in providers] == ["first", "second"]
        assert providers[0][1]["EDA_MODE"] == "first"
        assert providers[1][1]["EDA_MODE"] == "second"
        assert owner.dependencies_for(first.session_id).bridge_descriptor["endpoint"] == "first"
        assert owner.dependencies_for(second.session_id).provider_config["model"] == "second"
        assert bridges[0].sessions == [first.session_id]
        assert bridges[1].sessions == [second.session_id]
        first_config["model"] = "changed"
        first_environment["EDA_MODE"] = "changed"
        first_descriptor["endpoint"] = "changed"
        dependencies = owner.dependencies_for(first.session_id)
        assert dependencies.provider_config["model"] == "first"
        assert dependencies.environment["EDA_MODE"] == "first"
        assert dependencies.bridge_descriptor["endpoint"] == "first"
        with pytest.raises(TypeError):
            dependencies.environment["EDA_MODE"] = "invalid"
    finally:
        owner.close()
    assert all(bridge.closed for bridge in bridges)
    assert owner.controllers == {}


def test_reopening_a_session_cannot_change_its_captured_target_or_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sico.service.project_sessions.provider_from_config",
        lambda config, environment: SimpleNamespace(
            label=config["model"], base="test", model=config["model"]
        ),
    )
    owner = ProjectSessionOwner(tmp_path, provider_config={"model": "stable"},
                                environment={"EDA_MODE": "stable"}, bridge_factory=Bridge)
    try:
        controller = owner.open("a" * 32, {"endpoint": "one"}, context("target"))
        assert owner.open("a" * 32, {"endpoint": "one"}, context("target")) is controller
        with pytest.raises(ValueError, match="captured"):
            owner.open("a" * 32, {"endpoint": "two"}, context("other"))
    finally:
        owner.close()


@pytest.mark.parametrize("failure", ["provider", "bridge", "journal", "tools", "backend",
                                     "controller", "attach"])
def test_partial_session_build_releases_only_owned_resources(tmp_path, monkeypatch, failure):
    bridges = []

    class FailingBridge(Bridge):
        def register_session(self, session_id, bound):
            super().register_session(session_id, bound)
            if failure == "attach":
                raise RuntimeError("attach failed")

    def make_bridge(descriptor):
        if failure == "bridge":
            raise RuntimeError("bridge failed")
        bridge = FailingBridge(descriptor)
        bridges.append(bridge)
        return bridge

    if failure == "provider":
        monkeypatch.setattr(
            "sico.service.project_sessions.provider_from_config",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider failed")),
        )
    if failure == "journal":
        monkeypatch.setattr("sico.service.project_sessions.SessionJournal",
                            lambda *_args: (_ for _ in ()).throw(RuntimeError("journal failed")))
    if failure == "tools":
        monkeypatch.setattr("sico.service.project_sessions.context_tools",
                            lambda *_args: (_ for _ in ()).throw(RuntimeError("tools failed")))
    if failure == "backend":
        monkeypatch.setattr(
            "sico.service.project_sessions.create_backend",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("backend failed")),
        )
    if failure == "controller":
        monkeypatch.setattr(
            "sico.service.project_sessions.SessionController",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("controller failed")),
        )
    if failure != "provider":
        monkeypatch.setattr(
            "sico.service.project_sessions.provider_from_config",
            lambda *_args, **_kwargs: SimpleNamespace(
                label="test", base="test", model="test"
            ),
        )

    owner = ProjectSessionOwner(
        tmp_path, provider_config={"model": "test"}, bridge_factory=make_bridge
    )
    session_id = "c" * 32
    with pytest.raises(RuntimeError):
        owner.open(session_id, {"endpoint": "test"}, context("target"))
    assert owner.controllers == {}
    assert all(bridge.closed for bridge in bridges)
    directory = tmp_path / ".sico/ai/agent/sessions" / session_id
    if directory.exists():
        reopened = SessionJournal(tmp_path, session_id)
        reopened.close()
    owner.close()


def test_failed_second_session_does_not_close_first_session(tmp_path, monkeypatch):
    bridges = []
    calls = []

    def make_bridge(descriptor):
        bridge = Bridge(descriptor)
        bridges.append(bridge)
        return bridge

    def make_provider(config, environment):
        return SimpleNamespace(label=config["model"], base="test", model=config["model"])

    real_backend = __import__(
        "sico.service.project_sessions", fromlist=["create_backend"]
    ).create_backend

    def build_backend(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("second backend failed")
        return real_backend(*args, **kwargs)

    monkeypatch.setattr("sico.service.project_sessions.provider_from_config", make_provider)
    monkeypatch.setattr("sico.service.project_sessions.create_backend", build_backend)
    owner = ProjectSessionOwner(
        tmp_path, provider_config={"model": "test"}, bridge_factory=make_bridge
    )
    first = owner.open("a" * 32, {"endpoint": "first"}, context("first"))
    with pytest.raises(RuntimeError):
        owner.open("b" * 32, {"endpoint": "second"}, context("second"))
    assert owner.controllers == {first.session_id: first}
    assert not bridges[0].closed
    owner.close()
    assert bridges[0].closed


def test_pending_snapshot_is_bounded_and_owned_by_project_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sico.service.project_sessions.provider_from_config",
        lambda config, environment: SimpleNamespace(
            label=config["model"], base="test", model=config["model"]
        ),
    )
    owner = ProjectSessionOwner(tmp_path, provider_config={"model": "test"}, bridge_factory=Bridge)
    try:
        owner.open("a" * 32, {"endpoint": "first"}, context("first"))
        snapshot = owner.pending_work()
        assert snapshot.sessions == 1
        assert snapshot.queued == snapshot.approvals == snapshot.jobs == snapshot.reconcile == 0
    finally:
        owner.close()
    assert owner.pending_work().empty


def test_session_secrets_and_bridge_descriptor_stay_out_of_persistent_records(
        tmp_path, monkeypatch):
    secret = "session-secret-value"
    token = "bridge-secret-token"
    monkeypatch.setattr(
        "sico.service.project_sessions.provider_from_config",
        lambda config, environment: SimpleNamespace(
            label=config["model"], base="test", model=config["model"]
        ),
    )
    owner = ProjectSessionOwner(
        tmp_path,
        provider_config={"model": "test", "api_key_env": "SICO_API_KEY"},
        environment={"SICO_API_KEY": secret},
        bridge_factory=Bridge,
    )
    session_id = "d" * 32
    try:
        controller = owner.open(session_id, {"endpoint": "local", "token": token},
                                context("target"))
        controller.inbox.enqueue({
            "kind": "submit",
            "id": "1" * 32,
            "text": "retain this session",
            "context": controller.current.record(),
        }, controller.current, "chat")
        controller.history.record(controller.inbox.pending[-1])
    finally:
        owner.close()
    persisted = b"".join(
        path.read_bytes() for path in (tmp_path / ".sico/ai/agent").rglob("*")
        if path.is_file() and path.name not in {"writer.lock"}
    )
    assert persisted, "expected retained session records"
    assert secret.encode() not in persisted
    assert token.encode() not in persisted
    assert not (tmp_path / ".sico/ai/agent/service/discovery.json").exists()


def test_closing_one_session_releases_only_its_bundle(tmp_path, monkeypatch):
    bridges = []
    monkeypatch.setattr(
        "sico.service.project_sessions.provider_from_config",
        lambda config, environment: SimpleNamespace(
            label=config["model"], base="test", model=config["model"]
        ),
    )

    def make_bridge(descriptor):
        bridge = Bridge(descriptor)
        bridges.append(bridge)
        return bridge

    owner = ProjectSessionOwner(tmp_path, provider_config={"model": "test"},
                                bridge_factory=make_bridge)
    first_id, second_id = "a" * 32, "b" * 32
    try:
        owner.open(first_id, {"endpoint": "first"}, context("first"))
        second = owner.open(second_id, {"endpoint": "second"}, context("second"))
        assert owner.close_session(first_id) is True
        assert owner.close_session(first_id) is False
        assert owner.controllers == {second_id: second}
        assert bridges[0].closed
        assert not bridges[1].closed
    finally:
        owner.close()
    assert bridges[1].closed
