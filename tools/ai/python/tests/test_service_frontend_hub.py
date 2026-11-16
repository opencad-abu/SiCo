"""FrontendHub admission, dispatch and cleanup without a running service."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from sico.service.frontend_hub import FrontendHub
from sico.service.frontend_session import SessionToken
from sico.service.service_session_dto import SessionAddress
from sico.transport.framing import ProtocolError

PROJECT = "a" * 32
SERVICE = "b" * 32
RUNTIME = "c" * 32


class Pool:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def submit(self, function, *args):
        self.calls.append((function, args))
        if self.fail:
            raise RuntimeError("pool closed")


class Wire:
    def __init__(self, method="frontend.query"):
        self.method = method
        self.connection = "connection"
        self.control = "control"
        self.operation = "operation"
        self.closed = SimpleNamespace(is_set=lambda: False)
        self.executed = []
        self.closed_calls = 0

    def execute(self, payload):
        self.executed.append(payload)

    def close(self):
        self.closed_calls += 1


class Delegate:
    def __init__(self, value="delegate"):
        self.value = value
        self.calls = []
        self.closed = 0

    def execute(self, *args):
        self.calls.append(args)
        return self.value

    def events(self, *args):
        self.calls.append(args)
        return self.value

    def query(self, *args):
        self.calls.append(args)
        return self.value

    def run(self, *args):
        self.calls.append(args)
        return self.value

    def cancel(self, *args):
        self.calls.append(args)
        return self.value

    def close(self, *_args):
        self.closed += 1


class Cleanup:
    def __init__(self):
        self.started = 0

    def start(self):
        self.started += 1
        return self

    def wait(self, _timeout):
        return True


def hub():
    value = FrontendHub.__new__(FrontendHub)
    value.owner = SimpleNamespace(
        project="project",
        controllers={},
        dependencies_for=lambda _key: SimpleNamespace(bridge_descriptor={}),
    )
    value.descriptor = SimpleNamespace(project_id=PROJECT, service_id=SERVICE)
    value.control = SimpleNamespace()
    value.recovery = Delegate()
    value.ending = SimpleNamespace(wait=lambda _timeout: None)
    value.workspace = SimpleNamespace(close=lambda _timeout: None,
                                      catalog=SimpleNamespace(snapshot=lambda _v: {}))
    value.attach = Delegate()
    value.replay = Delegate()
    value.inputs = Delegate()
    value.knowledge_checks = Delegate()
    value.resource_checks = Delegate()
    value.pools = {"query": Pool(), "events": Pool(), "attach": Pool(), "command": Pool()}
    value.slots = {kind: threading.BoundedSemaphore(1) for kind in value.pools}
    value.closed = threading.Event()
    value._lock = threading.Lock()
    value._active = set()
    value._cleanup = Cleanup()
    return value


def test_start_tracks_one_entry_and_releases_capacity():
    value = hub()
    wire = Wire()
    value.start(wire, {"x": 1})
    function, args = value.pools["query"].calls[-1]
    assert value.pending == 1
    function(*args)
    assert wire.executed == [{"x": 1}]
    assert value.pending == 0
    assert value.slots["query"].acquire(blocking=False)
    value.slots["query"].release()


def test_start_rejects_closed_or_full_capacity_and_rolls_back_pool_failure():
    value = hub()
    wire = Wire()
    value.closed.set()
    with pytest.raises(ProtocolError, match="capacity unavailable"):
        value.start(wire, {})
    value.closed.clear()
    value.slots["query"].acquire()
    with pytest.raises(ProtocolError, match="capacity unavailable"):
        value.start(wire, {})
    value.slots["query"].release()
    value.pools["query"].fail = True
    with pytest.raises(RuntimeError, match="pool closed"):
        value.start(wire, {})
    assert value.pending == 0


def test_execute_dispatches_attach_events_query_commands_and_rejects_fields():
    value = hub()
    wire = Wire("frontend.attach")
    assert value.execute(wire, {"operation": "open", "params": {}}) == "delegate"
    assert value.attach.calls == [(wire, "open", {})]
    wire = Wire("frontend.events")
    assert value.execute(wire, {"operation": "events", "params": {}}) == "delegate"
    assert value.replay.calls == [(wire, "events", {})]
    wire = Wire("frontend.query")
    assert value.execute(wire, {"operation": "home", "params": {}}) == {
        "platforms": ["CDNS-IC"],
    }
    assert value.execute(wire, {
        "operation": "agent_knowledge", "params": {"q": 1},
    }) == "delegate"
    assert value.knowledge_checks.calls
    assert value.execute(wire, {
        "operation": "unknown", "params": {},
    }) == "delegate"
    assert value.replay.calls[-1] == (wire, "unknown", {})
    wire = Wire("frontend.command")
    value.command = lambda _wire, operation, params: ("command", operation, params)
    assert value.execute(wire, {"operation": "rename", "params": {"x": 1}}) == (
        "command", "rename", {"x": 1},
    )
    with pytest.raises(ProtocolError, match="Invalid frontend request"):
        value.execute(wire, {"operation": 1, "params": {}})
    with pytest.raises(ProtocolError, match="Invalid service message fields"):
        value.execute(wire, {"operation": "x"})


def test_controller_checks_project_service_and_runtime_identity():
    value = hub()
    controller = SimpleNamespace(runtime_id=RUNTIME)
    value.owner.controllers["session"] = controller
    address = SessionAddress(PROJECT, SERVICE, SessionToken("session", RUNTIME))
    row = address.record()
    assert value.controller(row) == (address, controller)
    wrong_service = dict(row, service_id="d" * 32)
    with pytest.raises(ProtocolError, match="another service"):
        value.controller(wrong_service)
    controller.runtime_id = "e" * 32
    with pytest.raises(ValueError, match="运行实例"):
        value.controller(row)


def test_close_cancels_active_wires_and_releases_owned_components():
    value = hub()
    wire = Wire()
    value._active.add((wire, object()))
    assert value.close(0.1) is True
    assert wire.closed_calls == 1
    assert value.closed.is_set()
    assert value.recovery.closed == 1
    assert value.replay.closed == 1
    assert value.attach.closed == 0
    assert value._cleanup.started == 1


def test_command_dispatch_covers_labels_cancellation_and_persisted_settings(monkeypatch):
    value = hub()
    value.workspace.labels = SimpleNamespace(command=lambda params: ("labels", params))
    value.knowledge_checks = Delegate("knowledge-cancel")
    value.resource_checks = Delegate("resource-cancel")
    assert value.command(Wire(), "navigation_labels", {"x": 1}) == ("labels", {"x": 1})
    assert value.command(Wire(), "agent_knowledge_cancel", {}) == "knowledge-cancel"
    assert value.command(Wire(), "agent_settings_check_cancel", {}) == "resource-cancel"

    from sico.service import feature_defaults, session_continuation

    monkeypatch.setattr(feature_defaults, "save",
                        lambda project, groups: ("saved", project, groups))
    assert value.command(Wire(), "feature_defaults_save", {"groups": ["a"]}) == (
        "saved", "project", ["a"],
    )
    wire = Wire()
    wire.operation = "op"
    monkeypatch.setattr(session_continuation, "continue_session",
                        lambda hub, wire, params: ("continued", params))
    assert value.command(wire, "continue_session", {"session_id": "s"}) == (
        "continued", {"session_id": "s"},
    )
    value.recovery.restore = lambda connection, operation, params: (
        "restored", connection, operation, params
    )
    assert value.command(wire, "recover_session", {"x": 1}) == (
        "restored", "connection", "op", {"x": 1},
    )


def test_command_control_and_end_status_use_typed_ownership():
    value = hub()
    value.owner.controllers["session"] = SimpleNamespace(runtime_id=RUNTIME)
    granted = SimpleNamespace(record=lambda: {"granted": True})
    value.control = SimpleNamespace(execute=lambda connection, params: granted)
    assert value.command(Wire(), "control", {"action": "acquire"}) == {"granted": True}

    address = SessionAddress(PROJECT, SERVICE, SessionToken("session", RUNTIME))
    value.ending = SimpleNamespace(
        observe=lambda _address, _op: SimpleNamespace(record=lambda: {"observed": True}),
        request=lambda _address, _op, _connection, _control: SimpleNamespace(
            record=lambda: {"ended": True}),
    )
    params = {"address": address.record(), "operation_id": "op"}
    wire = Wire()
    wire.operation = "op"
    assert value.command(wire, "end_status", params) == {"observed": True}
    assert value.command(wire, "end_session", params) == {"ended": True}
    value.control.run = lambda *_args: None
    with pytest.raises(ValueError, match="尚未开放"):
        value.command(Wire(), "unsupported", {
            "address": address.record(), "args": [], "kwargs": {},
        })


def test_frontend_query_reads_project_visible_drc_jobs_and_events(tmp_path):
    from cadai.visible_drc_registry import VisibleDrcRegistry

    job_ref = "visible-" + "a" * 32
    registry = VisibleDrcRegistry(tmp_path)
    registry.save({
        "schema": "cad.visible_drc.job.v1",
        "job_ref": job_ref,
        "window_ref": "wctx_fixture_1",
        "config_path": str(tmp_path / "drc.toml"),
        "work_root": str(tmp_path),
        "calibre_root": str(tmp_path / "calibre"),
        "timeout_seconds": 10,
        "candidate": str(tmp_path / "candidate.oasis"),
        "bbox": [0, 0, 100, 100],
        "top_cell": "inv",
        "size": 6,
        "sha256": "a" * 64,
        "warnings": ["visible_fragment"],
        "counts": {"output_shapes": 4},
        "config_sha256": "b" * 64,
        "runset_dependencies_sha256": "c" * 64,
        "recheck_of": "",
        "region_source": "visible",
        "state": "violations",
    })
    registry.append_event(job_ref, "queued")
    registry.append_event(job_ref, "violations")
    value = hub()
    value.owner.project = tmp_path
    listed = value.execute(Wire(), {
        "operation": "visible_drc_jobs",
        "params": {"action": "list"},
    })
    assert listed["jobs"][0]["job_ref"] == job_ref
    assert listed["jobs"][0]["state"] == "violations"
    detail = value.execute(Wire(), {
        "operation": "visible_drc_jobs",
        "params": {"action": "get", "job_ref": job_ref, "after": 1},
    })
    assert [event["state"] for event in detail["events"]] == ["violations"]
    assert detail["event_cursor"] == 2
    waited = value.execute(Wire(), {
        "operation": "visible_drc_jobs",
        "params": {
            "action": "wait",
            "job_ref": job_ref,
            "after": 1,
            "timeout_seconds": 1,
        },
    })
    assert [event["state"] for event in waited["events"]] == ["violations"]
    assert waited["timed_out"] is False
    from sico.service.visible_drc_query import query as visible_query

    ticks = iter([0.0, 0.05, 2.0])
    timed = visible_query(
        tmp_path,
        {
            "action": "wait",
            "job_ref": job_ref,
            "after": 99,
            "timeout_seconds": 1,
        },
        sleep=lambda _seconds: None,
        clock=lambda: next(ticks),
    )
    assert timed["timed_out"] is True
    assert timed["events"] == []
    running_ref = "visible-" + "d" * 32
    registry.save({
        "schema": "cad.visible_drc.job.v1",
        "job_ref": running_ref,
        "window_ref": "wctx_fixture_1",
        "config_path": str(tmp_path / "drc.toml"),
        "work_root": str(tmp_path),
        "calibre_root": str(tmp_path / "calibre"),
        "timeout_seconds": 10,
        "candidate": str(tmp_path / "candidate.oasis"),
        "bbox": [0, 0, 100, 100],
        "top_cell": "inv",
        "size": 6,
        "sha256": "a" * 64,
        "warnings": [],
        "counts": {},
        "config_sha256": "b" * 64,
        "runset_dependencies_sha256": "c" * 64,
        "recheck_of": "",
        "region_source": "visible",
        "state": "running",
    })
    registry.write_lease(running_ref, 4321)
    running_page = value.execute(Wire(), {
        "operation": "visible_drc_jobs",
        "params": {"action": "list"},
    })
    running_row = next(
        row for row in running_page["jobs"] if row["job_ref"] == running_ref
    )
    assert running_row["owner_active"] is True
    registry.write_lease(running_ref, 4321, updated_at=0)
    stale = value.execute(Wire(), {
        "operation": "visible_drc_jobs",
        "params": {"action": "get", "job_ref": running_ref},
    })
    assert stale["owner_active"] is False
    assert stale["recovery_required"] is True
    recovery_wait = value.execute(Wire(), {
        "operation": "visible_drc_jobs",
        "params": {
            "action": "wait",
            "job_ref": running_ref,
            "after": 99,
            "timeout_seconds": 1,
        },
    })
    assert recovery_wait["recovery_required"] is True
    assert recovery_wait["owner_active"] is False
    registry.write_lease(running_ref, 4321)
    requested = value.execute(Wire(), {
        "operation": "visible_drc_jobs",
        "params": {"action": "cancel", "job_ref": running_ref},
    })
    assert requested["cancel_requested"] is True
    assert registry.cancel_requested(running_ref) is True
    with pytest.raises(ValueError, match="action is invalid"):
        value.execute(Wire(), {
            "operation": "visible_drc_jobs",
            "params": {"action": "delete"},
        })

