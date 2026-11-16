"""Resource-release outcomes, identity races and committed binding reconciliation."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from agent_binding_fixtures import ReleaseGate, ResourceBroker

from sico.core.contracts import BoundContext, CircuitCallError, NeedsReconcile
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ScriptedProvider
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal
from sico.transport.framing import ProtocolError


@pytest.fixture
def session(tmp_path):
    with SessionJournal(tmp_path, "release") as journal:
        context = BoundContext("instance", "generation", "first", {})
        controller = SessionController(PythonBackend(ScriptedProvider([]), ToolRegistry(), journal,
                                                context))
        broker = ResourceBroker(controller)
        try:
            yield controller, broker
        finally:
            controller.close()
            assert controller.wait(5)


def test_reserved_submission_wins_and_release_does_not_dispatch(session):
    controller, broker = session
    key = controller.bindings.reserve_submission(controller.current)
    try:
        with pytest.raises(ValueError, match="active or queued"):
            controller.bindings.release_binding_resource("resource_0")
        assert not broker.calls and controller.bindings._resource_release is None
    finally:
        controller.bindings.release_submission(key)
    controller.bindings.release_binding_resource("resource_0")
    assert broker.calls == [(controller.session_id, "resource_0")]
    assert not controller.bindings.events.state["reservations"]
    assert controller.journal.events()[-1]["kind"] == "binding.resource_released"
    key = controller.bindings.reserve_submission(controller.current)
    controller.bindings.release_submission(key)
    assert not controller.bindings._submissions


@pytest.mark.parametrize("error", [
    ValueError("reservation is not owned"),
    CircuitCallError("binding_busy", "resource has unresolved work",
                     data={"operation_dispatched": False}),
])
def test_definite_refusal_keeps_resource_and_allows_a_new_submission(session, monkeypatch, error):
    controller, broker = session
    before = controller.bindings.events.state

    def refuse(*args):
        raise error

    monkeypatch.setattr(broker, "release_binding_resource", refuse)
    with pytest.raises(type(error)):
        controller.bindings.release_binding_resource("resource_0")
    assert controller.bindings.events.state is before
    assert controller.bindings._resource_release is None
    key = controller.bindings.reserve_submission(controller.current)
    controller.bindings.release_submission(key)
    assert not controller.bindings._submissions


@pytest.mark.parametrize("error", [NeedsReconcile("disconnected"), TimeoutError("timed out"),
                                  OSError("socket failed"), ProtocolError("Invalid JSON payload")])
@pytest.mark.parametrize("committed", [False, True])
def test_unknown_outcome_only_resolves_from_committed_evidence(
    session, monkeypatch, error, committed,
):
    controller, broker = session
    calls = []
    original = broker.release_binding_resource
    before = controller.bindings.events.cursor

    def interrupted(*args):
        calls.append(args)
        if committed:
            original(*args)
        raise error

    monkeypatch.setattr(broker, "release_binding_resource", interrupted)
    with pytest.raises(type(error)):
        controller.bindings.release_binding_resource("resource_0")
    assert controller.bindings.events.cursor == before
    assert controller.bindings._resource_release.unknown
    with pytest.raises(ValueError, match="待核对"):
        controller.bindings.reserve_submission(controller.current)
    with pytest.raises(ValueError, match="待核对"):
        controller.bindings.release_binding_resource("resource_0")
    controller.bindings.poll_binding_events()
    controller.bindings.poll_binding_events()
    assert len(calls) == 1
    if committed:
        assert controller.bindings._resource_release is None
        assert not controller.bindings.sync_error
        assert [row["kind"] for row in controller.journal.events()].count(
            "binding.resource_released") == 1
    else:
        assert controller.bindings._resource_release.unknown
        assert controller.bindings.events.state["reservations"]
        assert "待核对" in controller.bindings.sync_error


def test_resource_release_remains_blocked_until_local_journal_mirror_commits(session, monkeypatch):
    controller, broker = session
    append = controller.journal.append_binding
    before = controller.bindings.events.cursor

    def fail_mirror(*args):
        raise OSError("fsync failed")

    monkeypatch.setattr(controller.journal, "append_binding", fail_mirror)
    with pytest.raises(OSError, match="fsync failed"):
        controller.bindings.release_binding_resource("resource_0")
    assert controller.bindings.events.cursor == before
    assert controller.bindings.events.state["reservations"]
    assert not broker.binding_snapshot(controller.session_id)["reservations"]
    with pytest.raises(ValueError, match="待核对"):
        controller.bindings.reserve_submission(controller.current)
    monkeypatch.setattr(controller.journal, "append_binding", append)
    controller.bindings.poll_binding_events()
    assert controller.bindings._resource_release is None
    assert broker.calls == [(controller.session_id, "resource_0")]


@pytest.mark.parametrize("change", ["session", "runtime", "broker", "binding", "generation",
                                   "receipt", "snapshot", "operation", "close"])
def test_late_release_cannot_update_a_replaced_identity(session, monkeypatch, change):
    controller, broker = session
    original = broker.release_binding_resource
    saved_session = controller.session_id
    before = controller.bindings.events.state
    cursor = controller.bindings.events.cursor

    def respond(*args):
        result = original(*args)
        if change == "receipt":
            result["binding_id"] = "foreign"
        elif change == "snapshot":
            broker.registry.sessions[saved_session].binding_id = "foreign"
        return result

    gate = ReleaseGate(respond)
    monkeypatch.setattr(broker, "release_binding_resource", gate)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(controller.bindings.release_binding_resource, "resource_0")
        try:
            assert gate.entered.wait(5)
            # Taking the main lock while the RPC is paused is part of the contract.
            assert controller._lock.acquire(timeout=1)
            try:
                if change == "session":
                    controller.session_id = "replacement"
                elif change == "runtime":
                    controller.runtime_id = "replacement"
                elif change == "broker":
                    controller.bindings.broker = object()
                elif change in {"binding", "generation"}:
                    field = "binding_id" if change == "binding" else "generation"
                    controller.bindings.events.state = {**before, field: "replacement"}
                elif change == "operation":
                    controller.bindings._resource_release = object()
                elif change == "close":
                    controller.request_close()
            finally:
                controller._lock.release()
            assert not future.done()
        finally:
            gate.release.set()
        with pytest.raises((ValueError, NeedsReconcile)):
            future.result(5)
    assert controller.bindings.events.cursor == cursor
    assert controller.bindings.events.state["reservations"]
    if change == "operation":
        assert type(controller.bindings._resource_release) is object
    elif change in {"session", "runtime", "broker", "close"}:
        assert controller.bindings._resource_release is None
    else:
        assert controller.bindings._resource_release.unknown
    controller.session_id, controller.bindings.broker = saved_session, broker


def test_target_switch_and_competing_submit_during_resource_release(session, monkeypatch):
    controller, broker = session
    first = controller.current
    second = BoundContext(first.instance_id, first.generation, "second", {})
    broker.register_session(controller.session_id, second)
    controller.bindings.poll_binding_events()
    gate = ReleaseGate(broker.release_binding_resource)
    monkeypatch.setattr(broker, "release_binding_resource", gate)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(controller.bindings.release_binding_resource, "resource_0")
        try:
            assert gate.entered.wait(5)
            assert controller._lock.acquire(timeout=1)
            try:
                controller.bindings.select_binding_target(second.target_id)
            finally:
                controller._lock.release()
            for context in (first, second):
                with pytest.raises(ValueError, match="正在释放"):
                    controller.bindings.reserve_submission(context)
            # Direct non-Qt commands must respect the same release marker.
            with pytest.raises(ValueError, match="正在释放"):
                controller.submit("competing task", context=first)
            with pytest.raises(ValueError, match="正在释放"):
                controller.accept({"kind": "submit", "id": first.target_id, "text": "quick task",
                                   "context": first.record()})
            with pytest.raises(ValueError, match="正在释放"):
                controller.bindings.release_binding_resource("resource_0")
            assert controller.initialize_context() is False
            assert not controller.bindings._submissions and not future.done()
        finally:
            gate.release.set()
        future.result(5)
    assert controller.current == first and controller.bindings.events.default_context == second
    assert controller.bindings._resource_release is None
    controller.paused = True
    key = controller.bindings.reserve_submission(second)
    try:
        controller.submit("captured second", context=second, reservation=key)
    finally:
        controller.bindings.release_submission(key)
    assert controller.inbox.pending[0]["message"]["context"] == second.record()
    assert not controller.loop.provider.requests and len(broker.calls) == 1
