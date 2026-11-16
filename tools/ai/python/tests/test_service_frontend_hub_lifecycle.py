"""Frontend hub admission, worker completion and bounded shutdown."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from sico.api.frontend_session import SessionToken
from sico.api.service_session_dto import SessionAddress
from sico.core.contracts import BoundContext
from sico.service import (
    agent_settings,
    feature_defaults,
    knowledge_checks,
    record_deletion,
    resource_checks,
    visible_drc_policy,
    visible_drc_query,
)
from sico.service import frontend_hub as module
from sico.service.frontend_hub import FrontendHub
from sico.transport import host_discovery
from sico.transport.framing import ProtocolError


class FakeCleanup:
    def __init__(self, release, _name):
        self.release = release

    def start(self):
        self.release()
        return SimpleNamespace(wait=lambda _timeout: True)


class FakeRecovery:
    def __init__(self, *_args):
        self.closed = []
        self.retired = lambda _key: None

    def close(self, timeout=None):
        self.closed.append(timeout)

    def inspect(self, session_id, operation_id):
        return ("recovery", session_id, operation_id)

    def restore(self, connection, operation, params):
        return ("restore", connection, operation, params)


class FakeWorkspace:
    def __init__(self, _owner):
        self.catalog = SimpleNamespace(
            snapshot=lambda version: {"version": version},
            request=lambda: SimpleNamespace(result=lambda: None))
        self.labels = SimpleNamespace(command=lambda params: ("labels", params))
        self.worker = SimpleNamespace(
            session_query=lambda *_args, **_kwargs: SimpleNamespace(
                result=lambda: {"worker": True}),
            command=lambda *_args, **_kwargs: SimpleNamespace(
                result=lambda: {"command": True}))
        self.closed = []

    def close(self, timeout=None):
        self.closed.append(timeout)


class FakeAttach:
    def __init__(self, *_args):
        self.closed = []

    def close(self):
        self.closed.append(True)

    def execute(self, _wire, operation, params):
        return ("attach", operation, params)


class FakeReplay:
    def __init__(self, *_args):
        self.closed = []
        self.retired = []

    def close(self):
        self.closed.append(True)

    def retire(self, wire):
        self.retired.append(wire)

    def events(self, _wire, operation, params):
        return ("events", operation, params)

    def query(self, _wire, operation, params):
        return ("query", operation, params)


class FakeInputs:
    def __init__(self, *_args):
        pass

    def query(self, address, input_id):
        return SimpleNamespace(record=lambda: {
            "address": address.record(), "input_id": input_id})

    def controlled(self, *_args, **_kwargs):
        return SimpleNamespace(outcome="accepted", input_id="input")


class FakeChecks:
    def run(self, wire, params):
        return ("run", wire, params)

    def cancel(self, wire, params):
        return ("cancel", wire, params)


class FakeControl:
    def run(self, address, connection, proof, operation):
        return operation()

    def continue_session(self, _connection, _address):
        return SimpleNamespace(record=lambda: {"action": "acquire"})

    def execute(self, _connection, params):
        return SimpleNamespace(record=lambda: {"action": params["action"]})


class FakeEnding:
    def observe(self, address, operation):
        return SimpleNamespace(record=lambda: {"address": address.record(),
                                              "operation": operation})

    def request(self, address, operation, _connection, _control):
        return SimpleNamespace(record=lambda: {"state": "closing",
                                              "operation": operation})

    def wait(self, _timeout=None):
        return True


def build(monkeypatch, control=None):
    control = control or FakeControl()
    for name, value in (
        ("SessionControl", lambda *_args: control),
        ("SessionRecovery", FakeRecovery),
        ("ProjectWorkspace", FakeWorkspace),
        ("FrontendAttach", FakeAttach),
        ("FrontendReplay", FakeReplay),
        ("ServiceInputs", FakeInputs),
        ("SessionEnd", lambda *_args, **_kwargs: FakeEnding()),
        ("CleanupTask", FakeCleanup),
    ):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(resource_checks, "ResourceChecks", lambda _hub: FakeChecks())
    monkeypatch.setattr(knowledge_checks, "KnowledgeChecks", lambda _hub: FakeChecks())
    from contextlib import nullcontext

    owner = SimpleNamespace(
        project=object(),
        controllers={},
        _mutation=nullcontext(),
        creation=SimpleNamespace(create=lambda *_args: object()),
    )
    descriptor = SimpleNamespace(project_id="a" * 32, service_id="b" * 32)
    return FrontendHub(owner, descriptor)


class Wire:
    def __init__(self, method="frontend.attach"):
        self.method = method
        self.executed = threading.Event()
        self.closed = threading.Event()
        self.close_count = 0
        self.connection = object()
        self.control = object()

    def execute(self, payload):
        assert payload == {"operation": "status"}
        self.executed.set()

    def close(self):
        self.closed.set()
        self.close_count += 1


def test_hub_admits_one_worker_and_tracks_pending(monkeypatch):
    hub = build(monkeypatch)
    wire = Wire()
    try:
        hub.start(wire, {"operation": "status"})
        assert wire.executed.wait(5)
        deadline = time.monotonic() + 5
        while hub.pending and time.monotonic() < deadline:
            time.sleep(0.01)
        assert hub.pending == 0
    finally:
        hub.close()
    assert hub.closed.is_set()
    assert wire.close_count == 0


def test_hub_rejects_closed_and_exhausted_capacity(monkeypatch):
    hub = build(monkeypatch)
    hub.closed.set()
    with pytest.raises(ProtocolError, match="capacity"):
        hub.start(Wire(), {})
    hub.close()

    hub = build(monkeypatch)
    hub.slots["attach"] = threading.BoundedSemaphore(0)
    with pytest.raises(ProtocolError, match="capacity"):
        hub.start(Wire(), {})
    hub.close()


def test_hub_releases_slot_when_worker_submission_fails(monkeypatch):
    hub = build(monkeypatch)
    original = hub.pools["attach"]

    class BrokenPool:
        def submit(self, *_args):
            raise RuntimeError("pool closed")

        def shutdown(self, wait=True):
            original.shutdown(wait=wait)

    hub.pools["attach"] = BrokenPool()
    try:
        with pytest.raises(RuntimeError, match="pool closed"):
            hub.start(Wire(), {})
        assert hub.pending == 0
        assert hub.slots["attach"].acquire(blocking=False)
        hub.slots["attach"].release()
    finally:
        hub.pools["attach"] = original
        hub.close()


def test_hub_retires_replay_connection(monkeypatch):
    hub = build(monkeypatch)
    wire = Wire()
    try:
        hub.retire(wire)
        assert hub.replay.retired == [wire]
    finally:
        hub.close()


def test_hub_routes_queries_and_validates_envelopes(monkeypatch):
    hub = build(monkeypatch)
    wire = Wire()
    try:
        with pytest.raises(ProtocolError, match="service message fields"):
            hub.execute(wire, {})
        with pytest.raises(ProtocolError, match="Invalid frontend"):
            hub.execute(wire, {"operation": 1, "params": {}})
        assert hub.execute(wire, {"operation": "status", "params": {}}) == (
            "attach", "status", {})

        wire.method = "frontend.events"
        assert hub.execute(wire, {"operation": "events", "params": {}}) == (
            "events", "events", {})

        wire.method = "frontend.query"
        assert hub.execute(wire, {"operation": "home", "params": {}}) == {
            "platforms": ["CDNS-IC"]}
        monkeypatch.setattr(feature_defaults, "read", lambda _project: {"features": {}})
        assert hub.execute(wire, {"operation": "feature_defaults", "params": {}}) == {
            "features": {}}
        assert hub.execute(wire, {"operation": "catalog", "params": {"version": 3}}) == {
            "catalog": {"version": 3}, "sessions": []}
        assert hub.execute(wire, {
            "operation": "ask_head",
            "params": {"session_id": "missing", "input_id": "input"},
        }) is None
        assert hub.execute(wire, {
            "operation": "recovery",
            "params": {"session_id": "session", "operation_id": "input"},
        }) == ("recovery", "session", "input")
        assert hub.execute(wire, {
            "operation": "unknown", "params": {}}) == ("query", "unknown", {})
        with pytest.raises(ValueError, match="暂未支持"):
            hub.execute(wire, {
                "operation": "platform_list", "params": {"platform": "other"}})
    finally:
        hub.close()


def test_hub_controller_rejects_foreign_and_stale_sessions(monkeypatch):
    hub = build(monkeypatch)
    address = SessionAddress(
        "a" * 32, "b" * 32, SessionToken("session", "c" * 32))
    try:
        with pytest.raises(ValueError, match="运行实例已变化"):
            hub.controller(address.record())
        hub.owner.controllers["session"] = SimpleNamespace(runtime_id="c" * 32)
        assert hub.controller(address.record())[0] == address
        foreign = SessionAddress(
            "d" * 32, "b" * 32, SessionToken("session", "c" * 32))
        with pytest.raises(ProtocolError, match="another service"):
            hub.controller(foreign.record())
    finally:
        hub.close()


def test_hub_simple_commands_and_validation(monkeypatch):
    hub = build(monkeypatch)
    controller = SimpleNamespace(
        runtime_id="c" * 32,
        frontend_view=SimpleNamespace(context=object()),
    )
    hub.owner.controllers["session"] = controller
    record = SessionAddress(
        "a" * 32, "b" * 32, SessionToken("session", "c" * 32)).record()
    wire = Wire("frontend.command")
    wire.operation = "operation"
    base = {"address": record, "args": [], "kwargs": {}}
    try:
        assert hub.command(wire, "navigation_labels", {}) == ("labels", {})
        assert hub.command(wire, "agent_knowledge_cancel", {})[0] == "cancel"
        assert hub.command(wire, "agent_settings_check_cancel", {})[0] == "cancel"
        assert hub.command(wire, "recover_session", {})[0] == "restore"
        assert hub.command(wire, "control", {"action": "status"}) == {
            "action": "status"}

        monkeypatch.setattr(
            feature_defaults, "save",
            lambda project, groups: ("feature-save", project, groups))
        assert hub.command(
            wire, "feature_defaults_save", {"groups": {}})[0] == "feature-save"
        assert hub.command(wire, "end_status", {
            "address": record, "operation_id": "operation",
        })["operation"] == "operation"
        foreign = dict(record, project_id="d" * 32)
        with pytest.raises(ProtocolError, match="another service"):
            hub.command(wire, "end_status", {
                "address": foreign, "operation_id": "operation"})

        with pytest.raises(ProtocolError, match="creation"):
            hub.command(wire, "create", {**base, "args": []})
        with pytest.raises(ProtocolError, match="another service"):
            hub.command(wire, "create", {
                "address": foreign, "args": [], "kwargs": {}})
        with pytest.raises(ProtocolError, match="frontend command arguments"):
            hub.command(wire, "unknown", {**base, "args": None})
        with pytest.raises(ProtocolError, match="frontend input"):
            hub.command(wire, "submit", base)
        with pytest.raises(ProtocolError, match="no frontend arguments"):
            hub.command(wire, "attach_targets", {**base, "args": ["x"]})
        assert hub.command(wire, "attach_targets", base) is None
        with pytest.raises(ProtocolError, match="original task"):
            hub.command(wire, "query_interrupted", base)
        with pytest.raises(ProtocolError, match="recap"):
            hub.command(wire, "recap_start", base)
        with pytest.raises(ProtocolError, match="Unexpected command"):
            hub.command(wire, "resource_status", {**base, "args": ["x"]})
        with pytest.raises(ProtocolError, match="session name"):
            hub.command(wire, "rename", base)
        with pytest.raises(ValueError, match="尚未开放"):
            hub.command(wire, "unknown", base)
    finally:
        hub.close()


def test_hub_query_success_routes(monkeypatch):
    hub = build(monkeypatch)
    controller = SimpleNamespace(
        session_id="session", runtime_id="c" * 32)
    hub.owner.controllers["session"] = controller
    record = SessionAddress(
        "a" * 32, "b" * 32, SessionToken("session", "c" * 32)).record()
    wire = Wire("frontend.query")
    try:
        monkeypatch.setattr(
            agent_settings, "read",
            lambda _hub, params: {"settings": params})
        assert hub.execute(wire, {
            "operation": "agent_settings", "params": {"section": "model"},
        }) == {"settings": {"section": "model"}}

        monkeypatch.setattr(
            visible_drc_query, "query",
            lambda _project, params, requester: {
                "params": params, "requester": requester})
        assert hub.execute(wire, {
            "operation": "visible_drc_jobs", "params": {"limit": 1},
        })["requester"] == hub.descriptor.service_id

        monkeypatch.setattr(
            visible_drc_policy, "read", lambda _project: {"auto_watch": True})
        assert hub.execute(wire, {
            "operation": "visible_drc_policy", "params": {},
        }) == {"auto_watch": True}

        assert hub.execute(wire, {
            "operation": "ask_head",
            "params": {"session_id": "session", "input_id": "input"},
        }) == {"address": record, "input_id": "input"}

        monkeypatch.setattr(
            host_discovery, "list_instances", lambda _project, cancelled: ["node"])
        assert hub.execute(wire, {
            "operation": "platform_list", "params": {"platform": "CDNS-IC"},
        }) == ["node"]

        monkeypatch.setattr(
            record_deletion, "deletion_review",
            lambda _hub, session_id: {"session": session_id})
        assert hub.execute(wire, {
            "operation": "deletion_review", "params": {"session_id": "session"},
        }) == {"session": "session"}

        base = {"address": record, "args": [], "kwargs": {}}
        assert hub.execute(wire, {
            "operation": "tool_result", "params": base,
        }) == {"worker": True}
        assert hub.execute(wire, {
            "operation": "background",
            "params": {**base, "args": ["list_background"]},
        }) == {"worker": True}
        with pytest.raises(ValueError, match="后台任务修改"):
            hub.execute(wire, {
                "operation": "background",
                "params": {**base, "args": ["submit"]},
            })
    finally:
        hub.close()


def test_hub_create_submit_rename_and_status_commands(monkeypatch):
    hub = build(monkeypatch)
    controller = SimpleNamespace(
        session_id="session",
        runtime_id="c" * 32,
        frontend_view=SimpleNamespace(
            context=BoundContext("instance", "generation", "target", {})),
    )
    record = SessionAddress(
        "a" * 32, "b" * 32, SessionToken("session", "c" * 32)).record()
    wire = Wire("frontend.command")
    wire.operation = "operation"
    try:
        monkeypatch.setattr(
            module, "session_record",
            lambda _controller, _descriptor: {"address": record})
        created = hub.command(wire, "create", {
            "address": record,
            "args": [controller.frontend_view.context],
            "kwargs": {},
        })
        assert created["session"]["address"] == record
        assert created["control"]["action"] == "acquire"

        hub.owner.controllers["session"] = controller
        base = {"address": record, "args": [], "kwargs": {}}
        assert hub.command(wire, "resource_status", base) == {"command": True}
        assert hub.command(wire, "recap_start", {
            **base,
            "kwargs": {
                "expected_task": "task",
                "request_id": "request",
                "automatic": False,
            },
        }) == {"command": True}
        assert hub.command(wire, "submit", {
            **base, "args": ["input"]}) == "input"
        assert hub.command(wire, "rename", {
            **base, "args": ["renamed"]}) == {"command": True}
    finally:
        hub.close()
