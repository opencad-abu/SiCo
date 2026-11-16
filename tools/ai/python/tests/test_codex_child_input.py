"""Child request routing and original connection ownership across task boundaries."""

import json
import queue
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_agent_codex_native import CONTEXT
from test_codex_elicitation import event, resolved, running  # noqa: F401
from test_codex_elicitation_runtime import CONTENT, scope_for

from sico.codex.backend import CodexBackend
from sico.core.contracts import BoundContext
from sico.core.tools import ToolRegistry
from sico.orchestration import ChildTaskPolicy
from sico.orchestration import CopilotTaskOrchestrator
from sico.orchestration.thread_binding import ThreadBinding
from sico.core.session_events import EventCursor
from sico.service.workbench import attach_workbench
from sico.storage.workbench import WorkbenchIndex


def register(backend, name="child"):
    backend._record_subagent({"type": "subAgentActivity", "id": "spawn-" + name,
                             "agentThreadId": name, "agentPath": "/root/" + name,
                             "kind": "started"}, live=True)
    return backend.orchestration.children[name]


def request(kind="form", *, thread="child", turn="child-turn", rpc_id=7):
    if kind == "native":
        return {"id": rpc_id, "method": "item/tool/requestUserInput", "params": {
            "threadId": thread, "turnId": turn, "itemId": "input-" + str(rpc_id),
            "isBlocking": False, "questions": [{"id": "scope", "header": "范围",
                "question": "使用哪个检查范围？", "options": [
                    {"label": "全局", "description": "查看全部"},
                    {"label": "局部", "description": "仅查看指定范围"}]}]}}
    return {**event(threadId=thread, turnId=turn, mode=kind,
                    url="https://example.invalid/child", elicitationId="child-url"), "id": rpc_id}


def open_child(backend, kind="form", **kwargs):
    backend._input_request(request(kind, **kwargs), "turn")
    return next(reversed(backend.audit.index.audits.values()))


def answer_child(backend, controller, row, action="accept"):
    if "elicitation" in row:
        reply = {"action": action}
        if action == "accept" and row["elicitation"]["mode"] == "form":
            reply["content"] = CONTENT
        controller.backend_commands.elicitation(row["id"], scope=scope_for(controller, row),
                               response=reply, reply_id="reply-" + row["id"])
        return reply
    choice = "全局" if action == "accept" else "局部"
    backend.audit.answer(row["id"], {"scope": {"choice": choice, "text": ""}},
                         "reply-" + row["id"], "user", row["task_id"])
    return {"answers": {"scope": {"answers": [choice]}}}


def ended(thread="child", turn="child-turn", status="completed"):
    return {"method": "turn/completed", "params": {
        "threadId": thread, "turn": {"id": turn, "status": status}}}


@pytest.fixture
def child_running(running):  # noqa: F811
    backend, controller, sent = running
    backend.runtime.close = lambda: None
    backend.validate_audit_target = lambda: None
    register(backend)
    return backend, controller, sent


@pytest.mark.parametrize("kind,action", [(k, a) for k in ("form", "url", "native")
                                       for a in ("accept", "decline", "cancel")])
def test_child_answer_preserves_wire_identity_and_never_starts_task(child_running, kind, action):
    backend, controller, sent = child_running
    row = open_child(backend, kind)
    assert "/root/child" in row["title"]
    assert row["binding"]["connection_id"] == backend.connection_id
    assert row["binding"]["child_turn_id"] == "child-turn"
    if kind == "native" and action == "cancel":
        backend.audit.end_inputs("用户取消子任务交互", child_id="child")
        assert sent[0]["error"]["code"] == -32800
    else:
        reply = answer_child(backend, controller, row, action)
        assert not sent
        assert backend.audit.deliver() == 1
        assert sent == [{"id": 7, "result": reply}]
        assert row["status"] == "answered"
    backend._input_request(request(kind), "turn")
    assert len(sent) == 1 and not backend.audit.pending
    assert len([e for e in backend.journal.events() if e["kind"] == "task.started"]) == 1
    assert not controller.inbox.pending
    batch = controller.updates.read_updates()
    EventCursor(controller.session_id, controller.runtime_id, CONTEXT).consume(batch)
    cursor = EventCursor(controller.session_id, controller.runtime_id, CONTEXT)
    for entry in batch["events"]:
        cursor.consume({**batch, "events": [entry]})
    assert cursor.sequence == batch["events"][-1]["sequence"]
    assert WorkbenchIndex(backend.journal).sync().audits[row["id"]]["title"] == row["title"]


@pytest.mark.parametrize("kind", ["native", "form", "url"])
@pytest.mark.parametrize("after_parent", [False, True])
def test_consumer_keeps_child_after_parent_completion(child_running, kind, after_parent):
    backend, controller, sent = child_running
    frames = [request(kind), ended("thread", "turn")]
    if after_parent:
        frames.reverse()
    replies = []
    def next_frame():
        if frames:
            return frames.pop(0)
        row = next(reversed(backend.audit.index.audits.values()))
        if row["status"] == "pending":
            assert backend.active and backend.steering.turn_id is None
            replies.append(answer_child(backend, controller, row))
            raise queue.Empty
        assert sent == [{"id": 7, "result": replies[0]}]
        return ended()
    backend.runtime.rpc.next = next_frame
    calls = []
    backend.runtime.rpc.request = lambda method, params, **kw: calls.append(method) or {}
    events = list(backend._consume_turn("turn", backend.cancelled))
    assert events[-1]["kind"] == "task.completed"
    assert "turn/start" not in calls and not any(m.startswith("account/") for m in calls)
    assert not backend.audit.pending


@pytest.mark.parametrize("kind", ["native", "form", "url"])
@pytest.mark.parametrize("boundary", ["resolved", "completed", "failed", "interrupted", "closed"])
def test_child_terminal_invalidates_only_its_own_pending(child_running, kind, boundary):
    backend, controller, sent = child_running
    register(backend, "sibling")
    row = open_child(backend, kind)
    sibling = open_child(backend, "form", thread="sibling", turn="sibling-turn", rpc_id=8)
    answer_child(backend, controller, row)
    notification = (resolved(thread="child") if boundary == "resolved" else
                    {"method": "thread/closed", "params": {"threadId": "child"}}
                    if boundary == "closed" else ended(status=boundary))
    assert backend._notification(notification)
    assert row["status"] == "invalid" and sibling["status"] == "pending"
    assert not backend.stale and backend.active
    with pytest.raises(ValueError):
        answer_child(backend, controller, row)
    backend.audit.deliver()
    assert not any("result" in m and m["result"].get("action") == "accept" for m in sent)
    if boundary != "resolved":
        assert backend.orchestration.children["child"].turn_id == ""


@pytest.mark.parametrize("kind", ["native", "form"])
@pytest.mark.parametrize("when", ["before_open", "pending", "reply_received",
                                 "dispatching", "sent"])
def test_resolution_wins_and_cannot_reopen_request(child_running, monkeypatch, kind, when):
    backend, controller, sent = child_running
    notification = resolved(thread="child")
    row = None
    if when != "before_open":
        row = open_child(backend, kind)
    if when in {"reply_received", "dispatching", "sent"}:
        answer_child(backend, controller, row)
    if when == "dispatching":
        original = backend.audit.emit
        def emit(action, payload):
            original(action, payload)
            if action == "dispatching":
                backend._notification(notification)
        monkeypatch.setattr(backend.audit, "emit", emit)
        assert backend.audit.deliver() == 0
    else:
        if when == "sent":
            assert backend.audit.deliver() == 1
        backend._notification(notification)
    backend._input_request(request(kind), "turn")
    assert not backend.audit.pending
    assert len(sent) == (1 if when == "sent" else 0)
    if row:
        assert row["status"] == ("answered" if when == "sent" else "invalid")


@pytest.mark.parametrize("field", ["parent", "task", "context", "connection", "turn", "ended"])
def test_wrong_child_binding_rejected_without_affecting_root(child_running, field):
    backend, _, sent = child_running
    child = backend.child_inputs.bind("child", "child-turn")
    if field == "parent":
        foreign = CopilotTaskOrchestrator(ThreadBinding({"thread_id": "foreign"}))
        backend.orchestration.children["child"] = foreign.register("child", child.policy)
    elif field == "task":
        child.task_id = "old-task"
    elif field == "context":
        child.context = {}
    elif field == "connection":
        child.connection_id = "old-connection"
    elif field == "turn":
        child.turn_id = "other-turn"
    else:
        child.status = "completed"
    backend._input_request(request(), "turn")
    assert sent[0]["error"]["code"] == -32602
    assert not backend.audit.pending and backend.active and not backend.stale
    backend._input_request(event(), "turn")
    assert len(backend.audit.pending) == 1


@pytest.mark.parametrize("kind", ["native", "form"])
@pytest.mark.parametrize("change", ["context", "connection", "task", "child-turn"])
def test_late_answer_cannot_cross_child_scope(child_running, kind, change):
    backend, controller, sent = child_running
    row = open_child(backend, kind)
    answer_child(backend, controller, row)
    if change == "context":
        backend.state.context = BoundContext("instance", "generation", "other", {})
    elif change == "task":
        backend.state.task["id"] = "next-task"
    elif change == "child-turn":
        backend.orchestration.children["child"].turn_id = "next-turn"
    else:
        backend.runtime.rpc = SimpleNamespace(send=lambda message, **kwargs: sent.append(message))
    assert backend.audit.deliver() == 0
    assert not backend.audit.pending
    assert not any("answers" in m.get("result", {}) or m.get("result", {}).get("action") == "accept"
                   for m in sent)


def test_null_child_turn_requires_previous_observation(child_running):
    backend, _, sent = child_running
    backend._input_request(request(turn=None), "turn")
    assert sent[0]["error"]["code"] == -32602 and not backend.audit.pending
    backend.child_inputs.notification({"method": "turn/started", "params": {
        "threadId": "child", "turn": {"id": "child-turn", "status": "inProgress"}}})
    row = open_child(backend, turn=None, rpc_id=8)
    assert row["binding"]["turn_id"] is None
    assert row["binding"]["child_turn_id"] == "child-turn"


def test_unknown_history_parent_turn_and_reconnection_do_not_grant_ownership(child_running):
    backend, _, sent = child_running
    for req in [request(thread="unknown"), request(turn="turn", rpc_id=8)]:
        backend._input_request(req, "turn")
    backend._record_subagent({"agentThreadId": "history", "agentPath": "/root/old",
                             "kind": "started"})
    backend._input_request(request(thread="history"), "turn")
    previous = backend.connection_id
    backend.runtime.rpc = SimpleNamespace(send=lambda message, **kwargs: sent.append(message))
    backend.input_connection()
    assert previous != backend.connection_id
    backend._input_request(request(), "turn")
    assert len(sent) == 4 and all("error" in r for r in sent) and not backend.audit.pending


@pytest.mark.parametrize("kind", ["native", "form"])
def test_queued_parent_child_completion_wins_before_dispatch(child_running, kind):
    backend, controller, sent = child_running
    row = open_child(backend, kind)
    answer_child(backend, controller, row)
    notification = {"method": "item/completed", "params": {
        "threadId": "thread", "turnId": "turn", "item": {
            "type": "subAgentActivity", "id": "done", "kind": "completed",
            "agentThreadId": "child", "agentPath": "/root/child"}}}
    backend.runtime.rpc.poll_notifications = lambda: backend._notification(notification)
    assert backend.audit.deliver() == 0 and row["status"] == "invalid"
    assert len(sent) == 1
    assert sent[0].get("result", {}).get("action") != "accept"


@pytest.mark.parametrize("kind", ["native", "form"])
def test_idle_request_identity_cannot_be_rebound_to_next_task(child_running, kind):
    backend, _, sent = child_running
    backend.active = False
    idle = request(kind, thread="thread", turn=None if kind == "form" else "next-turn")
    assert backend._idle_request(idle)
    backend.active = True
    backend.audit.open(idle) if kind == "native" else backend.audit.elicitations.open(idle, "turn")
    assert len(sent) == 1 and not backend.audit.pending


@pytest.mark.parametrize("kind", ["native", "form"])
def test_duplicate_conflict_and_request_type_conflict_keep_original(child_running, kind):
    backend, _, sent = child_running
    row = open_child(backend, kind)
    changed = request(kind)
    if kind == "native":
        changed["params"]["questions"][0]["question"] = "替换问题"
    else:
        changed["params"]["message"] = "替换问题"
    backend._input_request(changed, "turn")
    backend._input_request(request("form" if kind == "native" else "native"), "turn")
    assert len(backend.audit.pending) == 1 and row["status"] == "pending"
    assert not sent
    reasons = [e["payload"]["reason"] for e in backend.journal.events()
               if e["kind"] == "codex.input_ignored"]
    assert reasons.count("conflicting_input_identity") == 2


@pytest.mark.parametrize("kind", ["native", "form"])
@pytest.mark.parametrize("stage,expected", [("pending", "withdrawn"),
    ("answer_received", "withdrawn"), ("dispatching", "unconfirmed")])
def test_child_recovery_never_reconstructs_mailbox(child_running, kind, stage, expected):
    backend, controller, sent = child_running
    row = open_child(backend, kind)
    if stage != "pending":
        answer_child(backend, controller, row)
    if stage == "dispatching":
        backend.audit.emit("dispatching", {"id": row["id"]})
    restored = CodexBackend(backend.provider, ToolRegistry(), backend.journal, CONTEXT)
    restored.workbench = attach_workbench(restored)
    assert restored.audit.index.audits[row["id"]]["status"] == expected
    assert not restored.audit.pending and not restored.child_inputs.active() and not sent
    backend.audit.finish()
    restored.close()


@pytest.mark.parametrize("kind", ["native", "form"])
@pytest.mark.parametrize("field", ["thread_id", "turn_id", "parent_thread_id", "parent_turn_id",
                                   "connection_id", "task_id", "context"])
def test_forged_child_origin_rejects_batch_atomically(child_running, kind, field):
    backend, controller, _ = child_running
    open_child(backend, kind)
    batch = deepcopy(controller.updates.read_updates())
    entry = next(e for e in batch["events"] if e["kind"] == "workbench.audit.opened")
    scope = (entry["payload"]["input_origin"] if kind == "native" else
             entry["payload"]["elicitation_origin"]["child_scope"])
    scope[field] = {} if field == "context" else "foreign"
    cursor = EventCursor(controller.session_id, controller.runtime_id, CONTEXT)
    with pytest.raises(ValueError):
        cursor.consume(batch)
    assert cursor.sequence == 0 and not cursor.native_origins.children
    assert cursor.consume(controller.updates.read_updates())


def test_multi_child_limit_login_exclusion_and_policy_conflict(child_running):
    backend, _, sent = child_running
    for i in range(5):
        name = "worker-" + str(i)
        register(backend, name)
        backend._input_request(request("native" if i % 2 else "form", thread=name,
                                      turn="work-" + str(i), rpc_id=i + 20), "turn")
    assert len(backend.audit.pending) == 4 and len(sent) == 1
    backend._input_request({**request(), "params": {**request()["params"],
        "mode": "openai/userVerification", "challenge": "DO-NOT-RECORD"}}, "turn")
    assert "DO-NOT-RECORD" not in json.dumps(backend.journal.events())
    assert sent[-1] == {"id": 7, "result": {"action": "decline"}}
    with pytest.raises(ValueError, match="policy"):
        backend.orchestration.register("child", ChildTaskPolicy("other", "other"))
    with pytest.raises(ValueError, match="owned"):
        backend.orchestration.register("thread", ChildTaskPolicy("fake", ""))


@pytest.mark.parametrize("kind", ["native", "form"])
def test_parent_cancel_and_close_discard_child_answers(child_running, kind):
    backend, controller, sent = child_running
    row = open_child(backend, kind)
    answer_child(backend, controller, row)
    backend.cancelled = lambda: True
    backend.runtime.rpc.request = lambda *args, **kw: {}
    backend.runtime.rpc.next = lambda: ended("thread", "turn", "interrupted")
    assert list(backend._consume_turn("turn", backend.cancelled))[-1]["kind"] == "task.cancelled"
    assert row["status"] == "invalid" and not backend.audit.pending
    assert not any("answers" in s.get("result", {}) for s in sent)
    backend._begin("Next task", CONTEXT, "next", "chat")
    backend.active, backend.cancelled = True, lambda: False
    backend.runtime = SimpleNamespace(rpc=SimpleNamespace(send=lambda message, **kwargs: sent.append(message)))
    backend._input_request(request(kind), "next-turn")
    assert sent[-1]["error"]["code"] == -32602
