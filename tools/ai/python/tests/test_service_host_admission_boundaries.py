"""Host Ask admission resolves durable routes before touching the inbox."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from sico.core.contracts import BoundContext
from sico.service import host_admission
from sico.service.host_admission import HostAdmission
from sico.service.service_business_dto import InputResult
from sico.transport.framing import ProtocolError

PROJECT = "a" * 32
SERVICE = "b" * 32


class Routes:
    def __init__(self):
        self.values = {}
        self.writes = []

    def read(self, _host, input_id):
        return self.values.get(input_id)

    def write(self, _host, input_id, value):
        self.values[input_id] = deepcopy(value)
        self.writes.append(input_id)


class Owner:
    def __init__(self, tmp_path):
        self.project = tmp_path
        self.controllers = {}
        self.opened = []
        self.dependencies_for = lambda _session: SimpleNamespace(
            bridge_descriptor={"bridge": 1})

    def open(self, session_id, descriptor, context, **kwargs):
        self.opened.append((session_id, descriptor, context, kwargs))
        return SimpleNamespace(session_id=session_id, runtime_id="c" * 32)


class Inputs:
    def __init__(self):
        self.accepted = []
        self.queries = []
        self.outcome = "accepted"

    def accept(self, address, input_id, text, context, **options):
        self.accepted.append((address, input_id, text, context, options))
        if self.outcome == "rejected":
            return InputResult(None, input_id, "rejected", "input_rejected")
        return InputResult(address, input_id, "accepted", execution="queued")

    def query(self, address, input_id):
        self.queries.append((address, input_id))
        return InputResult(address, input_id, "unknown")


def context():
    return BoundContext("instance", "generation", "bound",
                        {"cwd": "/project", "source": "relay"})


def request(**overrides):
    value = {
        "operation_id": "input",
        "method": "host.submit",
        "params": {
            "bridge": {"bridge": 1},
            "payload_id": "payload",
            "digest": "digest",
        },
    }
    value["params"].update(overrides)
    return value


def payload(sequence=1):
    return {
        "message": {"id": "input", "context": context().record(),
                    "text": "hello", "ask_sequence": sequence},
        "provider_config": None,
        "environment": {"PATH": "/bin"},
    }


class Bridge:
    def __init__(self, value):
        self.value = value
        self.registered = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def host_payload(self, _payload_id):
        return self.value

    def register_target(self, value):
        self.registered.append(value)


def admission(tmp_path, monkeypatch, value=None, route=None):
    owner, inputs = Owner(tmp_path), Inputs()
    instance = HostAdmission(owner, SimpleNamespace(
        project_id=PROJECT, service_id=SERVICE), inputs)
    routes = Routes()
    if route is not None:
        routes.values["input"] = route
    instance.routes = routes
    bridge = Bridge(value or payload())
    monkeypatch.setattr(host_admission, "HostRoutes", lambda *_args: routes)
    monkeypatch.setattr(host_admission, "host_identity",
                        lambda _bridge: {"instance_id": "instance",
                                         "generation": "generation"})
    monkeypatch.setattr(host_admission, "BridgeClient", lambda _descriptor: bridge)
    monkeypatch.setattr(host_admission, "capture_payload", lambda value: value)
    monkeypatch.setattr(host_admission, "digest", lambda _value: "digest")
    monkeypatch.setattr(host_admission, "claim_sequence",
                        lambda *_args: True)
    monkeypatch.setattr(host_admission, "validate_source", lambda _context: None)
    monkeypatch.setattr(host_admission, "select_destination",
                        lambda *_args: SimpleNamespace(session_id="new"))
    monkeypatch.setattr(host_admission, "agent_root", lambda _project: tmp_path)
    monkeypatch.setattr(host_admission.os.path, "samefile",
                        lambda *_args: True)
    monkeypatch.setattr(host_admission, "queue_event",
                        lambda *args, **kwargs: events.append((args, kwargs)))
    events = []
    return instance, owner, inputs, routes, bridge, events


def test_execute_dispatches_queries_and_logs_rejections(tmp_path, monkeypatch):
    instance, owner, inputs, _routes, _bridge, events = admission(tmp_path, monkeypatch)
    instance.query = lambda host, input_id: ("query", host, input_id)
    assert instance.execute({"operation_id": "input", "method": "host.input",
                             "params": {"host": "host"}}) == (
        "query", "host", "input")

    instance._submit = lambda _request: InputResult(
        None, "input", "rejected", "queue_full")
    result = instance.execute(request())
    assert result.outcome == "rejected"
    assert [row[0][0] for row in events] == ["received", "rejected"]


def test_submit_rejects_invalid_payload_provenance(tmp_path, monkeypatch):
    instance, _owner, _inputs, _routes, _bridge, _events = admission(tmp_path, monkeypatch)
    with pytest.raises(ProtocolError, match="payload changed"):
        instance._submit(request(digest="other"))

    bad = payload()
    bad["message"]["id"] = "other"
    instance, *_ = admission(tmp_path, monkeypatch, value=bad)
    with pytest.raises(ProtocolError, match="another operation"):
        instance._submit(request())

    instance, *_ = admission(tmp_path, monkeypatch, value=payload(sequence=2))
    with pytest.raises(ProtocolError, match="sequence changed"):
        instance._submit(request(ask_sequence=1))


def test_submit_reuses_a_rejected_or_accepted_route(tmp_path, monkeypatch):
    rejected = InputResult(None, "input", "rejected", "input_rejected")
    route = {"payload_digest": "digest", "rejection": rejected.record(),
             "address": None, "service_id": SERVICE}
    instance, _owner, inputs, routes, _bridge, _events = admission(
        tmp_path, monkeypatch, route=route)
    assert instance._submit(request()).outcome == "rejected"
    assert not inputs.accepted

    route = {"payload_digest": "digest", "address": None,
             "service_id": SERVICE, "session_id": "new"}
    instance, owner, inputs, routes, _bridge, _events = admission(
        tmp_path, monkeypatch, route=route)
    result = instance._submit(request())
    assert result.outcome == "accepted"
    assert owner.opened[0][0] == "new"
    assert routes.values["input"]["address"]["session_id"] == "new"


def test_submit_rejects_conflicts_and_stale_sessions(tmp_path, monkeypatch):
    route = {"payload_digest": "other", "address": None, "service_id": SERVICE}
    instance, _owner, _inputs, _routes, _bridge, _events = admission(
        tmp_path, monkeypatch, route=route)
    assert instance._submit(request()).code == "operation_conflict"

    route = {"payload_digest": "digest", "address": None,
             "service_id": "old", "session_id": "new"}
    instance, _owner, _inputs, _routes, _bridge, _events = admission(
        tmp_path, monkeypatch, route=route)
    assert instance._submit(request()).code == "stale_session"


def test_submit_creates_route_and_rejects_history_only_target(tmp_path, monkeypatch):
    instance, owner, inputs, routes, bridge, _events = admission(tmp_path, monkeypatch)
    instance.owner.controllers = {}
    history = tmp_path / "sessions/new"
    history.mkdir(parents=True)
    result = instance._submit(request())
    assert result.code == "history_readonly"
    assert bridge.registered == []

    history.rmdir()
    result = instance._submit(request())
    assert result.outcome == "accepted"
    assert bridge.registered and owner.opened
    assert routes.values["input"]["address"]["runtime_id"] == "c" * 32


def test_accept_persists_rejection_and_query_reads_route(tmp_path, monkeypatch):
    instance, _owner, inputs, routes, _bridge, _events = admission(tmp_path, monkeypatch)
    address = SimpleNamespace(record=lambda: {
        "project_id": PROJECT, "service_id": SERVICE,
        "session_id": "new", "runtime_id": "c" * 32})
    route = {"address": address.record(), "payload_digest": "digest"}
    inputs.outcome = "rejected"
    assert instance._accept("host", route, payload()["message"], {}).outcome == "rejected"
    assert routes.values["input"]["rejection"]["code"] == "input_rejected"

    assert instance.query("host", "missing").outcome == "unknown"
    assert instance.query("host", "input").outcome == "rejected"
    routes.values["input"].pop("rejection")
    routes.values["input"]["address"] = address.record()
    assert instance.query("host", "input").outcome == "unknown"
    assert inputs.queries


def test_refuse_capacity_is_durable_and_sequence_aware(tmp_path, monkeypatch):
    instance, _owner, _inputs, routes, _bridge, _events = admission(tmp_path, monkeypatch)
    params = {"bridge": {"bridge": 1}, "digest": "digest"}
    result = instance.refuse_capacity({"operation_id": "input", "params": params})
    assert result.code == "queue_full"
    assert routes.values["input"]["rejection"]["code"] == "queue_full"

    params["digest"] = "other"
    result = instance.refuse_capacity({"operation_id": "input", "params": params})
    assert result.code == "operation_conflict"
    monkeypatch.setattr(host_admission, "claim_sequence", lambda *_args: False)
    result = instance.refuse_capacity({"operation_id": "new", "params": {
        "bridge": {"bridge": 1}, "digest": "digest", "ask_sequence": 2}})
    assert result.code == "operation_conflict"
