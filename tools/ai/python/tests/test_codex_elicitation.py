"""MCP form validation, original request ownership and durable no-replay boundaries."""

import json
import queue
import threading
from collections import deque
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_agent_codex_native import CONTEXT
from test_codex_elicitation_runtime import CONTENT, FORM, scope_for

from sico.codex.backend import CodexBackend
from sico.codex.elicitation_schema import form_schema, response, web_url
from sico.codex.rpc import AppServer, RpcError
from sico.core.contracts import BoundContext
from sico.core.tools import ToolRegistry
from sico.service.controller import SessionController
from sico.core.session_events import EventCursor
from sico.service.workbench import attach_workbench
from sico.storage.journal import SessionJournal
from sico_ui.transcript import Transcript


@pytest.fixture
def running(tmp_path):
    with SessionJournal(tmp_path, "elicitation-unit") as journal:
        backend = CodexBackend(SimpleNamespace(options={}, environment={}),
                               ToolRegistry(), journal, CONTEXT)
        backend.workbench = attach_workbench(backend)
        backend._begin("Inspect revised scope", CONTEXT, "original", "chat")
        from codex_thread_fixture import bind_thread
        bind_thread(backend)
        backend.active, backend.cancelled = True, lambda: False
        backend._cancel_baseline = False
        backend.native_history.bind("turn")
        backend.steering.open("turn")
        sent = []
        backend.runtime = SimpleNamespace(rpc=SimpleNamespace(send=lambda message, **kwargs: sent.append(message)))
        controller = SessionController(backend)
        controller._running = True
        try:
            yield backend, controller, sent
        finally:
            controller._running = False
            backend.runtime = None
            backend.close()


def event(**params):
    return {"id": 7, "method": "mcpServer/elicitation/request", "params": {
        "threadId": "thread", "turnId": "turn", "serverName": "inspection",
        "mode": "form", "message": "请选择检查范围", "requestedSchema": deepcopy(FORM), **params,
    }}


def opened(backend, request=None):
    backend.audit.elicitations.open(request or event(), "turn")
    return next(reversed(backend.audit.index.audits.values()))


def accept(controller, row):
    return controller.backend_commands.elicitation(row["id"], scope=scope_for(controller, row),
        response={"action": "accept", "content": CONTENT}, reply_id="reply")


def resolved(request_id=7, thread="thread"):
    return {"method": "serverRequest/resolved", "params": {
        "threadId": thread, "requestId": request_id}}


def test_typed_answer_dedup_null_turn_and_read_only_history(running):
    backend, controller, sent = running
    row = opened(backend, event(turnId=None))
    scope = scope_for(controller, row)
    assert row["binding"]["turn_id"] is None
    assert row["binding"]["host_turn_id"] == "turn"
    assert accept(controller, row)
    assert accept(controller, row) is False
    with pytest.raises(ValueError, match="不能覆盖"):
        controller.backend_commands.elicitation(row["id"], scope=scope,
                              response={"action": "cancel"}, reply_id="new")
    assert not sent
    assert backend.audit.deliver() == 1
    assert sent == [{"id": 7, "result": {"action": "accept", "content": CONTENT}}]
    backend.audit.elicitations.open(event(turnId=None), "turn")
    backend.audit.elicitations.resolved(resolved())
    assert backend.audit.deliver() == 0 and len(sent) == 1
    assert row["status"] == "answered"
    backend._finish("completed")
    cursor = EventCursor(controller.session_id, controller.runtime_id, CONTEXT)
    transcript = Transcript(read_only=True)
    for entry in cursor.consume(controller.updates.read_updates()):
        transcript.receive(entry)
    assert len(transcript.audits) == 1
    text = str(transcript.messages)
    assert "revised" in text and "做对照" in text and "服务结果另行核对" in text
    assert not controller.inbox.pending


@pytest.mark.parametrize("field", ["session_id", "runtime_id", "task_id", "context", "binding"])
def test_foreign_scope_does_not_answer_or_send(running, field):
    backend, controller, sent = running
    row = opened(backend)
    scope = deepcopy(scope_for(controller, row))
    scope[field] = {} if field in {"context", "binding"} else "foreign"
    with pytest.raises(ValueError):
        controller.backend_commands.elicitation(row["id"], scope=scope, response={"action": "cancel"}, reply_id="x")
    assert row["status"] == "pending" and not sent


@pytest.mark.parametrize("change", ["cancel", "stale", "turn", "connection", "context"])
def test_answer_received_then_scope_changes_never_delivers_accept(running, change):
    backend, controller, sent = running
    row = opened(backend)
    accept(controller, row)
    if change == "cancel":
        backend.cancelled = lambda: True
    elif change == "stale":
        backend.stale = True
    elif change == "turn":
        backend.steering.end()
        backend.steering.open("next")
    elif change == "connection":
        backend.runtime = SimpleNamespace(rpc=SimpleNamespace(send=lambda message, **kwargs: sent.append(message)))
    else:
        backend.state.context = BoundContext("instance", "generation", "other", {})
    assert backend.audit.deliver() == 0
    assert row["status"] == "invalid" and not backend.audit.pending
    assert all(message["result"]["action"] == "cancel" for message in sent)


@pytest.mark.parametrize("when", ["before_open", "pending", "answered_locally"])
def test_service_resolved_request_cannot_be_revived(running, when):
    backend, controller, sent = running
    interaction = backend.audit.elicitations
    if when != "before_open":
        row = opened(backend)
        if when == "answered_locally":
            accept(controller, row)
    interaction.resolved(resolved())
    interaction.open(event(), "turn")
    assert not interaction._pending() and not sent
    if when != "before_open":
        assert row["status"] == "invalid"
        with pytest.raises(ValueError):
            accept(controller, row)


def test_duplicate_conflict_foreign_resolved_and_pending_limit(running):
    backend, _, sent = running
    row = opened(backend)
    interaction = backend.audit.elicitations
    interaction.resolved(resolved(thread="child"))
    interaction.resolved(resolved(request_id=[]))
    interaction.open(event(), "turn")
    assert len(interaction._pending()) == 1
    with pytest.raises(RpcError, match="identity changed"):
        interaction.open(event(message="Different decision"), "turn")
    for number in range(8, 12):
        interaction.open({**event(), "id": number}, "turn")
    assert len(interaction._pending()) == 4
    assert sent == [{"id": 11, "result": {"action": "decline"}}]
    interaction.end("ended")
    interaction.end("ended twice")
    assert len(sent) == 5 and row["status"] == "invalid"


@pytest.mark.parametrize("params", [
    {"mode": "openai/userVerification", "challenge": "private-challenge"},
    {"mode": "openai/form"}, {"mode": []}, {"requestedSchema": {"type": "object"}},
    {"requestedSchema": {"type": "object", "properties": {"x": {"type": []}}}},
    {"mode": "url", "url": "https://user:secret@example.com", "elicitationId": "url"},
    {"threadId": "child"}, {"turnId": "old"},
])
def test_unsupported_and_foreign_requests_are_rejected_without_copying_challenges(running, params):
    backend, _, sent = running
    backend.audit.elicitations.open(event(**params), "turn")
    assert not backend.audit.pending and not backend.audit.index.audits
    assert len(sent) == 1
    assert "private-challenge" not in json.dumps(backend.journal.events())
    assert "secret" not in json.dumps(backend.journal.events())


@pytest.mark.parametrize("stage,sends", [("reply_received", 0), ("dispatching", 0), ("resumed", 1)])
def test_journal_failure_never_retries_native_response(running, monkeypatch, stage, sends):
    backend, controller, sent = running
    row = opened(backend)
    original = backend.journal.append
    def append(kind, *args):
        if kind == "workbench.audit." + stage:
            raise OSError("disk full")
        return original(kind, *args)
    if stage != "reply_received":
        accept(controller, row)
    monkeypatch.setattr(backend.journal, "append", append)
    with pytest.raises(OSError, match="disk full"):
        if stage == "reply_received":
            accept(controller, row)
        else:
            backend.audit.deliver()
    monkeypatch.undo()
    assert backend.audit.deliver() == 0
    assert len(sent) == sends
    if stage == "resumed":
        backend._finish("needs_reconcile")
        backend.audit.index.sync()
        assert row["status"] == "unconfirmed"


def test_blocked_reply_send_does_not_hold_backend_lock(running):
    backend, controller, sent = running
    row = opened(backend)
    accept(controller, row)
    entered, release = threading.Event(), threading.Event()
    def blocked(message, **kwargs):
        entered.set()
        assert release.wait(1)
    backend.runtime.rpc.send = blocked
    worker = threading.Thread(target=backend.audit.deliver)
    worker.start()
    assert entered.wait(1)
    acquired = backend.lock.acquire(timeout=.2)
    assert acquired
    backend.lock.release()
    release.set()
    worker.join(1)
    assert not worker.is_alive()
    assert row["status"] == "answered"


def test_send_failure_is_unconfirmed_and_pending_close_on_journal_error(running, monkeypatch):
    backend, controller, _ = running
    row = opened(backend)
    accept(controller, row)
    def fail(*args, **kwargs):
        raise RpcError("pipe closed")
    backend.runtime.rpc.send = fail
    with pytest.raises(RpcError, match="unconfirmed"):
        backend.audit.deliver()
    assert row["status"] == "unconfirmed"
    assert backend.audit.deliver() == 0
    opened(backend, {**event(), "id": 8})
    opened(backend, {**event(), "id": 9})
    closed = []
    backend.runtime.close = lambda: closed.append("runtime")
    backend.bridge = SimpleNamespace(close=lambda: closed.append("bridge"))
    def disk_full(*args):
        raise OSError("disk full")
    monkeypatch.setattr(backend.journal, "append", disk_full)
    with pytest.raises(OSError, match="disk full"):
        backend.close()
    assert closed == ["runtime", "bridge"]
    assert not backend.audit.pending and not backend.steering.turn_id
    monkeypatch.undo()


@pytest.mark.parametrize("stage,expected", [("pending", "withdrawn"),
    ("answer_received", "withdrawn"), ("dispatching", "unconfirmed")])
def test_process_recovery_does_not_reconstruct_pending_rpc(running, stage, expected):
    backend, controller, sent = running
    row = opened(backend)
    if stage != "pending":
        accept(controller, row)
    if stage == "dispatching":
        backend.audit.elicitations.emit("dispatching", {"id": row["id"], "reply_id": "reply"})
    restored = CodexBackend(backend.provider, ToolRegistry(), backend.journal, CONTEXT)
    restored.workbench = attach_workbench(restored)
    assert restored.audit.index.audits[row["id"]]["status"] == expected
    assert not restored.audit.pending and restored.runtime is None and not sent
    backend.audit.finish()
    restored.close()


@pytest.mark.parametrize("field", ["thread_id", "turn_id", "task_id", "context"])
def test_public_elicitation_origin_validation_is_atomic(running, field):
    backend, controller, _ = running
    opened(backend)
    batch = deepcopy(controller.updates.read_updates())
    row = next(e for e in batch["events"] if e["kind"] == "workbench.audit.opened")
    row["payload"]["elicitation_origin"][field] = {} if field == "context" else "foreign"
    cursor = EventCursor(controller.session_id, controller.runtime_id, CONTEXT)
    with pytest.raises(ValueError, match="another task"):
        cursor.consume(batch)
    assert cursor.sequence == 0 and not cursor.native_origins.tasks
    assert cursor.consume(controller.updates.read_updates())


def test_idle_drain_cancels_unbound_request_and_retains_unrelated_frames(running):
    backend, _, sent = running
    rpc = AppServer.__new__(AppServer)
    rpc.pending = deque([{"method": "unknown"}, event()])
    rpc.messages = queue.Queue()
    rpc.messages.put({"id": 99, "result": {}})
    rpc.send = lambda message, **kwargs: sent.append(message)
    backend.runtime.rpc, backend.active = rpc, False
    rpc.poll_requests(backend._idle_request)
    assert sent == [{"id": 7, "result": {"action": "cancel"}}]
    assert list(rpc.pending) == [{"method": "unknown"}, {"id": 99, "result": {}}]
    assert not backend.audit.pending


@pytest.mark.parametrize("boundary", ["completed", "cancelled"])
def test_actual_turn_consumer_settles_pending_before_terminal_event(running, boundary):
    backend, controller, sent = running
    row = opened(backend)
    if boundary == "cancelled":
        backend.cancelled = lambda: True
    calls = []
    backend.runtime.close = lambda: None
    backend.runtime.rpc.request = lambda *args, **kwargs: calls.append(args) or {}
    frames = iter([{"method": "turn/completed", "params": {
        "threadId": "thread", "turn": {"id": "turn", "status": boundary}}}])
    backend.runtime.rpc.next = lambda: next(frames)
    events = list(backend._consume_turn("turn", backend.cancelled))
    assert events[-1]["kind"] == "task." + boundary
    assert not backend.audit.pending and row["status"] == "invalid"
    assert sent == [{"id": 7, "result": {"action": "cancel"}}]
    # Model-only cancellation stops locally; external work remains covered by
    # the interrupt/reconcile regression in test_interaction_cancel.
    assert not any(call[0] == "turn/interrupt" for call in calls)
    with pytest.raises(ValueError):
        accept(controller, row)


def test_queued_resolution_wins_before_dispatch(running):
    backend, controller, sent = running
    row = opened(backend)
    accept(controller, row)
    backend.runtime.rpc.poll_notifications = lambda: backend._notification(resolved())
    assert backend.audit.deliver() == 0
    assert row["status"] == "invalid" and not sent


@pytest.mark.parametrize("boundary", ["cancel", "resolved"])
def test_boundary_during_durable_dispatch_never_sends_answer(running, monkeypatch, boundary):
    backend, controller, sent = running
    row = opened(backend)
    accept(controller, row)
    original = backend.audit.elicitations.emit
    def emit(kind, payload, **kwargs):
        original(kind, payload, **kwargs)
        if kind == "dispatching":
            if boundary == "cancel":
                backend.cancelled = lambda: True
            else:
                backend.audit.elicitations.resolved(resolved())
    monkeypatch.setattr(backend.audit.elicitations, "emit", emit)
    assert backend.audit.deliver() == 0
    assert row["status"] == "invalid" and not backend.audit.pending
    assert sent == ([{"id": 7, "result": {"action": "cancel"}}] if boundary == "cancel" else [])


def test_zero_false_optional_and_named_options_remain_typed():
    schema = form_schema({"type": "object", "properties": {
        "zero": {"type": "number", "minimum": 0, "maximum": 0},
        "false": {"type": "boolean", "default": False},
        "legacy": {"type": "string", "enum": ["x", "y"], "enumNames": ["甲", "乙"]},
        "multi": {"type": "array", "items": {"anyOf": [{"const": "y", "title": "乙"}]}},
        "optional": {"type": "string"}}, "required": ["zero", "false"]})
    value = {"action": "accept", "content": {"zero": 0, "false": False, "multi": ["y"]}}
    assert response({"mode": "form", "schema": schema}, value) == value
    integer_schema = form_schema({"type": "object", "properties": {
        "i": {"type": "integer", "default": 17.0}}})
    normalized = response({"mode": "form", "schema": integer_schema},
                          {"action": "accept", "content": {"i": 17.0}})
    assert type(normalized["content"]["i"]) is int
    for field in [{"type": "integer", "maximum": 10 ** 1000}, {"type": []},
                  {"type": "string", "enum": ["x"], "enumNames": []}]:
        with pytest.raises(ValueError):
            form_schema({"type": "object", "properties": {"x": field}})
    for invalid in [None, [], {"action": []}, {"action": "accept", "content": {"zero": True}}]:
        with pytest.raises(ValueError):
            response({"mode": "form", "schema": schema}, invalid)


@pytest.mark.parametrize("url", ["file:///tmp/x", "javascript:alert(1)", "https://x:bad/",
    "https://user:pass@example.com", "https://example.com/\n", "https://[bad"])
def test_unsafe_urls_are_never_browser_targets(url):
    with pytest.raises(ValueError):
        web_url(url)
