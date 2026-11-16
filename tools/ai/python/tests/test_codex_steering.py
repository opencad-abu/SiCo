"""Steering identity, failure and durable no-replay contracts."""

from sico.service.frontend_session import SessionToken

from copy import deepcopy
from types import SimpleNamespace

import pytest
from agent_sessions_harness import DesktopSessions
from test_agent_codex_native import CONTEXT

from sico.codex.backend import CodexBackend
from sico.codex.rpc import RpcError, RpcRejected
from sico.core.contracts import BoundContext
from sico.core.tools import ToolRegistry
from sico.service.controller import SessionController
from sico.core.session_events import EventCursor
from sico.storage.journal import SessionJournal
from sico_ui.transcript import Transcript


@pytest.fixture
def running(tmp_path):
    with SessionJournal(tmp_path, "steer-unit") as journal:
        backend = CodexBackend(SimpleNamespace(options={}, environment={}),
                               ToolRegistry(), journal, CONTEXT)
        backend._begin("Original task", CONTEXT, "original", "chat")
        from codex_thread_fixture import bind_thread
        bind_thread(backend)
        backend.active, backend.cancelled = True, lambda: False
        backend.native_history.bind("turn-one")
        backend.steering.open("turn-one")
        controller = SessionController(backend)
        controller._running = True
        try:
            yield backend, controller
        finally:
            controller._running = False
            backend.runtime = None
            backend.close()


def rpc_for(backend, response=None, error=None):
    calls = []
    def request(method, params, **kwargs):
        calls.append((method, params, kwargs))
        if error:
            raise error
        return response if response is not None else {"turnId": "turn-one"}
    backend.runtime = SimpleNamespace(rpc=SimpleNamespace(request=request))
    return calls


@pytest.mark.parametrize("field", ["session_id", "runtime_id", "thread_id", "turn_id", "task_id"])
def test_stale_or_foreign_identity_is_rejected_before_rpc(running, field):
    backend, controller = running
    calls = rpc_for(backend)
    scope = controller.backend_commands.steering_scope()
    scope[field] = "foreign"
    with pytest.raises(ValueError):
        controller.backend_commands.steer("Supplement", scope=scope, request_id="edit")
    assert not calls and not backend.steering.pending and not controller.inbox.pending


def test_changed_design_and_quick_input_cannot_steer_another_target(running):
    backend, controller = running
    scope = controller.backend_commands.steering_scope()
    scope["context"] = BoundContext("instance", "generation", "other-window", {}).record()
    with pytest.raises(ValueError, match="来源"):
        controller.backend_commands.steer("Supplement", scope=scope, request_id="wrong_source")
    # A host QuickInput with that target still follows its existing durable queue.
    controller.accept({"kind": "submit", "id": "other-window", "text": "Read other target",
                       "context": scope["context"]})
    assert len(controller.inbox.pending) == 1
    assert controller.inbox.pending[0]["message"]["context"] == scope["context"]
    assert not backend.steering.pending


@pytest.mark.parametrize("boundary", ["cancel", "complete", "question", "new_turn"])
def test_queued_supplement_never_crosses_turn_boundary(running, boundary):
    backend, controller = running
    calls = rpc_for(backend)
    scope = controller.backend_commands.steering_scope()
    receipt = controller.backend_commands.steer("Supplement", scope=scope, request_id="edit")
    if boundary == "cancel":
        backend.cancelled = lambda: True
    elif boundary == "question":
        backend.state.task["waiting_audits"] = ["question"]
    else:
        backend.steering.end("turn-one")
        if boundary == "new_turn":
            backend.steering.open("turn-two")
    backend.steering.dispatch()
    assert receipt.result(1)["status"] == "rejected"
    assert not calls and not controller.inbox.pending


@pytest.mark.parametrize("response,error,status", [
    ({"turnId": "turn-one"}, None, "accepted"),
    (None, RpcRejected("turn mismatch"), "rejected"),
    (None, RpcError("timeout"), "unconfirmed"),
    ({"turnId": "foreign"}, None, "unconfirmed"),
])
def test_receipt_dedup_no_retry_and_replay(running, response, error, status):
    backend, controller = running
    calls = rpc_for(backend, response, error)
    scope = controller.backend_commands.steering_scope()
    receipt = controller.backend_commands.steer("Supplement", scope=scope, request_id="edit")
    assert controller.backend_commands.steer("Supplement", scope=scope, request_id="edit") is receipt
    with pytest.raises(ValueError, match="冲突"):
        controller.backend_commands.steer("Changed", scope=scope, request_id="edit")
    backend.steering.dispatch()
    assert receipt.result(1)["status"] == status
    backend.steering.dispatch()
    assert len(calls) == 1
    assert controller.backend_commands.steer("Supplement", scope=scope, request_id="edit") is receipt
    assert backend.state.task["steer_blocked"] == (status == "unconfirmed")
    backend.steering.end()
    backend._finish("completed")
    cursor = EventCursor(controller.session_id, controller.runtime_id, CONTEXT)
    events = cursor.consume(controller.updates.read_updates())
    transcript = Transcript(read_only=True)
    for event in events:
        transcript.receive(event)
    assert len(transcript.supplements) == 1
    assert "Supplement" in transcript.supplements["edit"]["text"]
    assert backend.state.task["status"] == "completed"


def test_failed_durable_acceptance_never_sends(running, monkeypatch):
    backend, controller = running
    calls = rpc_for(backend)
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(backend.journal, "append", fail)
    with pytest.raises(OSError, match="disk full"):
        controller.backend_commands.steer("Supplement", scope=controller.backend_commands.steering_scope(), request_id="edit")
    assert not backend.steering.pending and not calls
    monkeypatch.undo()


@pytest.mark.parametrize("stage,status,sends", [
    ("sending", "rejected", 0), ("accepted", "unconfirmed", 1),
    ("rejected", "rejected", 0),
])
def test_journal_failure_settles_receipt_and_never_retries(
    running, monkeypatch, stage, status, sends,
):
    backend, controller = running
    calls = rpc_for(backend)
    receipt = controller.backend_commands.steer("Supplement", scope=controller.backend_commands.steering_scope(), request_id="edit")
    original = backend.journal.append

    def append(kind, *args):
        if kind == "codex.steer." + stage:
            raise OSError("disk full")
        return original(kind, *args)

    monkeypatch.setattr(backend.journal, "append", append)
    if stage == "rejected":
        backend.cancelled = lambda: True
    with pytest.raises(OSError, match="disk full"):
        backend.steering.dispatch()
    assert receipt.result(1)["status"] == status
    assert receipt.result()["persisted"] is False
    backend.steering.dispatch()
    assert len(calls) == sends
    assert not backend.steering.pending
    assert backend.state.task["steer_blocked"]


def test_close_releases_runtime_even_when_receipt_cannot_be_saved(running, monkeypatch):
    backend, controller = running
    receipt = controller.backend_commands.steer("Supplement", scope=controller.backend_commands.steering_scope(), request_id="edit")
    closed = []
    backend.runtime = SimpleNamespace(close=lambda: closed.append("runtime"))
    backend.bridge = SimpleNamespace(close=lambda: closed.append("bridge"))

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(backend.journal, "append", fail)
    with pytest.raises(OSError, match="disk full"):
        backend.close()
    assert receipt.result(1)["status"] == "rejected"
    assert closed == ["runtime", "bridge"]
    assert backend.runtime is None and backend.bridge is None and not backend.active


def test_late_rpc_reply_after_end_does_not_overwrite_uncertain_receipt(running):
    backend, controller = running
    receipt = controller.backend_commands.steer("Supplement", scope=controller.backend_commands.steering_scope(), request_id="edit")

    def request(*args, **kwargs):
        backend.steering.end()
        return {"turnId": "turn-one"}

    backend.runtime = SimpleNamespace(rpc=SimpleNamespace(request=request))
    backend.steering.dispatch()
    assert receipt.result(1)["status"] == "unconfirmed"
    assert backend.steering.rows["edit"]["status"] == "unconfirmed"
    assert not any(k.startswith("steer_") for k in backend.state.task)


@pytest.mark.parametrize("field", ["thread_id", "turn_id", "task_id", "context"])
def test_public_receipt_validates_recorded_turn_and_source_atomically(running, field):
    backend, controller = running
    controller.backend_commands.steer("Supplement", scope=controller.backend_commands.steering_scope(), request_id="edit")
    batch = deepcopy(controller.updates.read_updates())
    row = next(e for e in batch["events"] if e["kind"] == "codex.steer.queued")
    row["payload"]["scope"][field] = {} if field == "context" else "foreign"
    cursor = EventCursor(controller.session_id, controller.runtime_id, CONTEXT)
    with pytest.raises(ValueError, match="another task"):
        cursor.consume(batch)
    assert cursor.sequence == 0 and not cursor.native_origins.tasks
    assert cursor.consume(controller.updates.read_updates())


def test_command_worker_does_not_wait_for_native_receipt(running):
    backend, controller = running
    sessions = DesktopSessions(controller)
    try:
        outer = sessions.worker.command(SessionToken(controller.session_id, controller.runtime_id), "steer", "Supplement",
            scope=controller.backend_commands.steering_scope(), request_id="edit")
        # A second command must finish even though no turn consumer has dispatched steer.
        status = sessions.worker.command(SessionToken(controller.session_id, controller.runtime_id), "resource_status").result(2)
        assert status["busy"] and not outer.done()
        sessions.worker.command(SessionToken(controller.session_id, controller.runtime_id), "cancel").result(2)
        backend.cancelled = controller._cancel.is_set
        backend.steering.dispatch()
        assert outer.result(2)["status"] == "rejected"
    finally:
        controller._running = False
        sessions.close()
        assert sessions.wait(5)


@pytest.mark.parametrize("last_status,expected", [
    ("queued", "rejected"), ("sending", "unconfirmed"),
])
def test_restart_retains_uncertain_input_without_replay(tmp_path, last_status, expected):
    provider = SimpleNamespace(options={}, environment={})
    with SessionJournal(tmp_path, "recover-steer") as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        backend._begin("Original", CONTEXT, "original", "chat")
        from codex_thread_fixture import bind_thread
        bind_thread(backend)
        backend.active, backend.cancelled = True, lambda: False
        backend.native_history.bind("turn")
        backend.steering.open("turn")
        backend.steering.submit("Supplement", backend.steering.identity(), "edit")
        if last_status == "sending":
            backend.steering.record("edit", "sending")
        # Simulate process loss without graceful close.
    with SessionJournal(tmp_path, "recover-steer") as journal:
        restored = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        assert restored.steering.rows["edit"]["status"] == expected
        assert restored.steering.pending is None and restored.runtime is None
        assert restored.state.task["status"] == "cancelled"
        assert not any(k.startswith("steer_") for k in restored.state.task)
