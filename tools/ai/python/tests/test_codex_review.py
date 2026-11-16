"""Review cancellation uses its bound public execution turn without granting input rights."""

import queue
from collections import deque
from copy import deepcopy, copy

import pytest
from test_agent_codex_native import CONTEXT
from test_codex_thread_operations import wired

from sico.codex.rpc import AppServer, RpcError, RpcRejected
from sico.core.contracts import BoundContext
from sico.storage.journal import SessionJournal

ACTION = {"kind": "review", "target": {"type": "custom", "instructions": "Review source"}}


def entry(method="item/started", key="entry", thread="thread", turn="outer"):
    return {"method": method, "params": {"threadId": thread, "turnId": turn,
        "item": {"id": key, "type": "enteredReviewMode"}}}


def started(turn="internal", thread="thread"):
    return {"method": "turn/started", "params": {"threadId": thread,
        "turn": {"id": turn, "status": "inProgress"}}}


def completed(turn="outer", thread="thread"):
    return {"method": "turn/completed", "params": {"threadId": thread,
        "turn": {"id": turn, "status": "interrupted"}}}


@pytest.fixture
def review(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "review") as journal:
        loop, calls = wired(journal, monkeypatch)
        loop._begin("Review", CONTEXT, "review", "thread_operation")
        loop.active = True
        loop.thread_ops.prepare(deepcopy(ACTION))
        rpc = loop.runtime.rpc
        rpc.pending = deque([entry(), entry("item/completed"), started()])
        rpc.next = lambda: rpc.pending.popleft()
        rpc.defer = lambda frames: AppServer.defer(rpc, frames)
        rpc.sent = []
        rpc.send = lambda message, **kwargs: rpc.sent.append(message)
        original = rpc.request

        def request(method, params, **kwargs):
            if method == "review/start":
                calls.append((method, params))
                return {"reviewThreadId": "thread", "turn": {"id": "outer", "status": "inProgress"}}
            return original(method, params)

        rpc.request = request
        yield loop, calls, rpc
        loop.close()


def test_review_cancel_uses_internal_id_but_keeps_outer_task_identity(review):
    loop, calls, rpc = review
    original = list(rpc.pending)
    rpc.pending.extend([{"method": "thread/tokenUsage/updated", "params": {
        "threadId": "thread", "turnId": "internal", "tokenUsage": {
            "last": {"inputTokens": 400, "outputTokens": 30}}}}, completed()])
    outer = loop.thread_ops.start(ACTION)
    assert list(rpc.pending)[:3] == original  # Startup does not consume normal evidence.
    assert not rpc.sent
    list(loop._turn(outer, lambda: True))
    assert loop.state.task["status"] == "cancelled"
    assert loop.state.task["input_tokens"] == loop.state.task["output_tokens"] == 0
    assert [(m, p) for m, p in calls if m == "turn/interrupt"] == [
        ("turn/interrupt", {"threadId": "thread", "turnId": "internal"})]
    events = loop.journal.events()
    assert [e["payload"]["native"]["turn_id"] for e in events
            if e["kind"] == "codex.turn.bound"] == ["outer"]
    mapping = next(e for e in events if e["kind"] == "codex.review.bound")
    assert mapping["task_id"] == loop.state.task["id"]
    assert mapping["payload"]["context"] == CONTEXT.record()
    assert mapping["payload"]["connection_id"] == loop.connection_id
    loop.thread_ops.finish()
    assert loop.thread_ops.review.binding is None


def test_review_internal_mapping_does_not_authorize_input_or_foreign_completion(review):
    loop, _, rpc = review
    loop.thread_ops.start(ACTION)
    rpc.pending.extend([{"id": 27, "method": "item/permissions/requestApproval", "params": {
        "threadId": "thread", "turnId": "internal"}}, completed("internal"),
        {"method": "item/completed", "params": {"threadId": "thread", "turnId": "outer",
            "item": {"id": "final", "type": "agentMessage", "text": "Outer final"}}},
        {"method": "turn/completed", "params": {"threadId": "thread",
            "turn": {"id": "outer", "status": "completed"}}}])
    list(loop._turn("outer", lambda: False))
    assert loop.state.task["status"] == "completed"
    assert loop.state.task["text"] == "Outer final"
    assert rpc.sent == [{"id": 27, "error": {
        "code": -32602, "message": "Input request does not match a live registered turn"}}]


@pytest.mark.parametrize("frames", [
    [started(), completed()],
    [entry(thread="foreign"), entry("item/completed", thread="foreign"), started(), completed()],
    [entry(turn="old"), entry("item/completed", turn="old"), started(), completed()],
    [entry("item/completed"), started()],
    [entry(), started()],
    [entry(), entry("item/completed", key="other"), started()],
    [entry(), entry("item/completed"), started("outer")],
    [entry(), entry("item/completed"), started("")],
    [entry(), entry("item/completed"), started("old")],
])
def test_review_requires_current_entry_sequence_and_fresh_execution_turn(review, frames):
    loop, calls, rpc = review
    loop.native_history.bind("old")
    rpc.pending = deque(frames)
    with pytest.raises(RpcError):
        loop.thread_ops.start(ACTION)
    assert loop.thread_ops.review.binding is None
    assert not any(m == "turn/interrupt" for m, _ in calls)
    assert not any(e["kind"] == "codex.review.bound" for e in loop.journal.events())
    assert list(rpc.pending) == frames


def test_deferred_review_frames_preserve_order_with_bounded_memory(review):
    _, _, rpc = review
    frames = list(rpc.pending)
    rpc.defer([started("unrelated")])
    assert list(rpc.pending) == [started("unrelated"), *frames]
    with pytest.raises(RpcError, match="buffer exceeded"):
        rpc.defer([started()] * 4096)
    assert list(rpc.pending) == [started("unrelated"), *frames]


@pytest.mark.parametrize("boundary", ["thread", "connection", "task", "context", "rpc", "closed"])
def test_review_mapping_cannot_cross_source_or_connection_boundary(review, boundary):
    loop, calls, rpc = review
    loop.thread_ops.start(ACTION)
    if boundary == "thread":
        from codex_thread_fixture import bind_thread
        bind_thread(loop, "foreign")
    elif boundary == "connection":
        loop.runtime.rpc = copy(loop.runtime.rpc)
        loop.input_connection()
    elif boundary == "task":
        loop.state.task["id"] = "new"
    elif boundary == "context":
        loop.state.context = BoundContext(CONTEXT.instance_id, CONTEXT.generation,
                                           CONTEXT.target_id, {"valid": True, "view": "new"})
    elif boundary == "rpc":
        loop.runtime.rpc = object()
    else:
        loop.active = False
    with pytest.raises(RpcError, match="changed"):
        loop.thread_ops.interrupt("outer")
    assert not any(m == "turn/interrupt" for m, _ in calls)


def test_review_start_scope_change_does_not_publish_binding(review):
    loop, _, rpc = review
    original = rpc.request
    def request(method, params, **kwargs):
        result = original(method, params)
        if method == "review/start":
            loop.runtime.rpc = copy(loop.runtime.rpc)
            loop.input_connection()
        return result
    rpc.request = request
    with pytest.raises(RpcError, match="changed"):
        loop.thread_ops.start(ACTION)
    assert loop.thread_ops.review.binding is None


def test_review_mapping_is_never_reconstructed_from_journal(review):
    loop, calls, _ = review
    loop.thread_ops.start(ACTION)
    from sico.codex.thread_operations import ThreadOperations
    restored = ThreadOperations(loop, loop.journal.events())
    assert restored.review.binding is None
    assert restored.receipt["status"] == "needs_reconcile"
    restored.action = ACTION
    with pytest.raises(RpcError, match="no bound"):
        restored.interrupt("outer")
    assert not any(m == "turn/interrupt" for m, _ in calls)


def test_review_interrupt_failure_has_one_dispatch_and_no_alternate_target(review):
    loop, calls, rpc = review
    loop.thread_ops.start(ACTION)
    original = rpc.request
    def request(method, params, **kwargs):
        if method == "turn/interrupt":
            calls.append((method, params))
            raise RpcRejected("Rejected cancellation")
        return original(method, params)
    rpc.request = request
    with pytest.raises(RpcRejected):
        list(loop._turn("outer", lambda: True))
    assert [(m, p) for m, p in calls if m == "turn/interrupt"] == [
        ("turn/interrupt", {"threadId": "thread", "turnId": "internal"})]


def test_review_mapping_is_durable_before_it_can_cancel(review, monkeypatch):
    loop, calls, _ = review
    original = loop._event
    def record(kind, payload):
        if kind == "codex.review.bound":
            raise OSError("Disk full")
        return original(kind, payload)
    monkeypatch.setattr(loop, "_event", record)
    with pytest.raises(OSError):
        loop.thread_ops.start(ACTION)
    assert loop.thread_ops.review.binding is None
    assert not any(m == "turn/interrupt" for m, _ in calls)


def test_review_rechecks_connection_after_durable_cancel_intent(review, monkeypatch):
    loop, calls, _ = review
    loop.thread_ops.start(ACTION)
    original = loop._event
    def record(kind, payload):
        result = original(kind, payload)
        if kind == "codex.review.interrupt_requested":
            loop.runtime.rpc = copy(loop.runtime.rpc)
            loop.input_connection()
        return result
    monkeypatch.setattr(loop, "_event", record)
    with pytest.raises(RpcError, match="changed"):
        loop.thread_ops.interrupt("outer")
    assert not any(m == "turn/interrupt" for m, _ in calls)


@pytest.mark.parametrize("receipt", [
    {"reviewThreadId": "foreign", "turn": {"id": "outer", "status": "inProgress"}},
    {"reviewThreadId": "thread", "turn": None},
    {"reviewThreadId": "thread", "turn": {"id": "", "status": "inProgress"}},
    {"reviewThreadId": "thread", "turn": {"id": "outer", "status": "completed"}},
])
def test_invalid_review_receipt_never_binds(review, receipt):
    loop, _, rpc = review
    original = rpc.request
    rpc.request = lambda m, p: receipt if m == "review/start" else original(m, p)
    with pytest.raises(RpcError, match="receipt"):
        loop.thread_ops.start(ACTION)
    assert loop.thread_ops.review.binding is None


@pytest.mark.parametrize("boundary", ["cancelled", "stale", "timeout"])
def test_review_start_wait_is_bounded_and_retains_frames(review, monkeypatch, boundary):
    loop, calls, rpc = review
    original = rpc.request
    def request(method, params, **kwargs):
        result = original(method, params)
        if method == "review/start":
            loop.cancelled = lambda: boundary == "cancelled"
            loop.stale = boundary == "stale"
        return result
    rpc.request = request
    frames = list(rpc.pending)
    if boundary == "timeout":
        from sico.codex import review as module
        clock = iter([0, 0, 100])
        monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
        rpc.next = lambda: (_ for _ in ()).throw(queue.Empty())
    with pytest.raises(RpcError):
        loop.thread_ops.start(ACTION)
    assert loop.thread_ops.review.binding is None
    assert list(rpc.pending) == frames
    assert not any(m == "turn/interrupt" for m, _ in calls)
