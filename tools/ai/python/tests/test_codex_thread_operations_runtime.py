"""Original 0.154 thread controls through a controlled localhost provider."""

import json
import threading

import pytest
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
    registry,
    settings,
    sse,
)

from sico.codex.backend import CodexBackend
from sico.codex.rpc import AppServer
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal


def operate(backend, action, cancelled=lambda: False):
    events = list(backend.thread_ops.run(action, cancelled, context=CONTEXT, input_id=None))
    assert backend.state.task["status"] == "completed", events[-8:]
    return events


@pytest.mark.parametrize("tool_format", ["native", "flat"])
def test_goal_budget_continuation_clear_and_restore(
    tmp_path, gateway, native_codex, tool_format, monkeypatch,  # noqa: F811
):
    calls, frames = [], []
    original, original_next = AppServer.request, AppServer._next

    def request(self, method, params, **kwargs):
        calls.append((method, params))
        return original(self, method, params, **kwargs)

    def receive(self, timeout):
        event = original_next(self, timeout)
        frames.append(event)
        return event

    monkeypatch.setattr(AppServer, "request", request)
    monkeypatch.setattr(AppServer, "_next", receive)
    provider = settings(gateway, native_codex, tool_format)
    with SessionJournal(tmp_path, "goals") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            gateway.responses.put(answer("Ordinary task."))
            assert list(backend.run("Ordinary task"))[-1]["kind"] == "task.completed"
            definitions = [t for g in gateway.requests[0]["tools"] for t in g.get("tools", [g])]
            assert not any(t.get("name") == "create_goal" for t in definitions)
            before = len(calls)
            for text in ("First goal step.", "Second goal step."):
                gateway.responses.put(answer(text))
            events = operate(backend, {"kind": "goal_start", "objective": "Collect test evidence",
                                       "token_budget": 20})
            goal = backend.thread_ops.goals.value
            assert goal["status"] == "budgetLimited" and goal["tokensUsed"] == 30
            assert len(gateway.requests) == 3
            assert not any(m == "turn/start" for m, _ in calls[before:])
            turns = [e for e in events if e["kind"] == "codex.turn.bound"]
            assert len(turns) == 2 and len({e["task_id"] for e in turns}) == 1
            assert backend.state.task["turn"] == 2
            assert backend.runtime is None
            backend.resume_thread()
            assert len(gateway.requests) == 3
            operate(backend, {"kind": "goal_clear"})
            assert backend.thread_ops.goals.value is None
            assert any(e["kind"] == "codex.goal.cleared" for e in journal.events())
        finally:
            backend.close()
            (tmp_path / "proof.json").write_text(json.dumps({"calls": calls, "frames": frames,
                "requests": gateway.requests, "events": journal.events()}, ensure_ascii=False))
    with SessionJournal(tmp_path, "goals") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            backend.resume_thread()
            assert backend.thread_ops.goals.value is None and len(gateway.requests) == 3
        finally:
            backend.close()


def test_cancel_goal_pauses_before_interrupt_and_never_replays(
    tmp_path, gateway, native_codex, monkeypatch,  # noqa: F811
):
    entered, release = threading.Event(), threading.Event()
    methods = []
    original = AppServer.request

    def request(self, method, params, **kwargs):
        methods.append((method, params))
        return original(self, method, params, **kwargs)

    monkeypatch.setattr(AppServer, "request", request)

    def blocked(_request):
        entered.set()
        assert release.wait(15)
        return answer("Late result")

    gateway.responses.put(blocked)
    provider = settings(gateway, native_codex, "native")
    with SessionJournal(tmp_path, "cancel-goal") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        controller = SessionController(backend)
        try:
            controller.thread_commands.thread_operation({"kind": "goal_start", "objective": "User goal",
                "token_budget": 100}, scope=controller.thread_commands.thread_scope(), request_id="explicit")
            assert entered.wait(8)
            controller.cancel()
            assert controller.wait(10)
            assert backend.state.task["status"] == "cancelled", journal.events()[-8:]
            assert backend.thread_ops.goals.value["status"] == "paused"
            pause = next(i for i, (m, p) in enumerate(methods)
                         if m == "thread/goal/set" and p.get("status") == "paused")
            interrupt = next(i for i, (m, _) in enumerate(methods) if m == "turn/interrupt")
            assert pause < interrupt
            release.set()
            backend.resume_thread()
            assert len(gateway.requests) == 1
        finally:
            release.set()
            controller.close()
            assert controller.wait(10)


def test_rollback_to_exact_turn_boundary_runtime(tmp_path, gateway, native_codex):  # noqa: F811
    with SessionJournal(tmp_path, "rollback-to") as journal:
        backend = CodexBackend(settings(gateway, native_codex, "native"), registry([]),
                               journal, CONTEXT)
        try:
            gateway.responses.put(answer("First turn."))
            list(backend.run("First turn"))
            gateway.responses.put(answer("Second turn."))
            events = list(backend.run("Second turn"))
            turn = next(event["payload"]["native"]["turn_id"] for event in events
                        if event["kind"] == "codex.turn.bound")
            reverted = operate(backend, {"kind": "rollback_to", "before_turn_id": turn})
            boundary = next(event["payload"] for event in reverted
                            if event["kind"] == "codex.history.reverted")
            assert boundary["before_turn_id"] == turn
            assert boundary["removed_turn_ids"] == [turn]
            thread = backend.runtime.rpc.request(
                "thread/read", {"threadId": backend.thread_id, "includeTurns": True})
            assert len(thread["thread"]["turns"]) == 1
        finally:
            backend.close()


def test_fork_rollback_review_compact_and_memory_readback(
    tmp_path, gateway, native_codex, monkeypatch,  # noqa: F811
):
    frames = []
    receive = AppServer._next
    original_request = AppServer.request

    def request(self, method, params, **kwargs):
        if method == "thread/start":
            params = {**params, "historyMode": "paginated"}
        return original_request(self, method, params, **kwargs)

    def capture(self, timeout):
        event = receive(self, timeout)
        frames.append(event)
        return event

    monkeypatch.setattr(AppServer, "_next", capture)
    monkeypatch.setattr(AppServer, "request", request)
    provider = settings(gateway, native_codex, "native")
    with SessionJournal(tmp_path, "operations") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            for text in ("Original first", "Original second"):
                gateway.responses.put(answer(text))
                assert list(backend.run(text))[-1]["kind"] == "task.completed"
            original = backend.thread_id
            orchestration, collaboration = backend.orchestration, backend.collaboration
            backend._record_collaboration({
                "type": "collabAgentToolCall", "id": "ownership-proof", "tool": "spawnAgent",
                "senderThreadId": original, "receiverThreadIds": ["historical-child"],
            })
            child = orchestration.children["historical-child"]
            operate(backend, {"kind": "fork"})
            fork = backend.thread_id
            assert fork != original
            assert backend.orchestration is orchestration
            assert backend.collaboration is collaboration
            assert orchestration.parent_thread_id == collaboration.parent_thread_id == fork
            assert child.parent_thread_id == original
            assert not orchestration.active()
            operate(backend, {"kind": "rollback", "num_turns": 1, "history_only": True})
            rpc = backend.runtime.rpc
            source = rpc.request("thread/read", {"threadId": original, "includeTurns": True})
            branch = rpc.request("thread/read", {"threadId": fork, "includeTurns": True})
            assert len(source["thread"]["turns"]) == 2
            assert len(branch["thread"]["turns"]) == 1
            gateway.responses.put(answer("Review complete."))
            operate(backend, {"kind": "review", "target": {
                "type": "custom", "instructions": "Review the existing conversation."}})
            gateway.responses.put(answer("Conversation summary."))
            events = operate(backend, {"kind": "compact"})
            assert any(e["kind"] == "codex.turn.bound" for e in events)
            count = len(gateway.requests)
            operate(backend, {"kind": "memory", "settings": {
                "enabled": False, "generate": False, "use": False}})
            memory = backend.thread_ops.memory.snapshot()
            assert memory["configured"] == memory["effective"]
            assert memory["scope"] == "project"
            assert backend.runtime.home == journal.root / "codex"
            backend.close()
            backend.resume_thread()
            backend.thread_ops.memory.inspect()
            assert backend.thread_id == fork
            assert orchestration.parent_thread_id == collaboration.parent_thread_id == fork
            assert orchestration.children["historical-child"] is child
            assert backend.thread_ops.memory.snapshot() == memory
            assert len(gateway.requests) == count
        finally:
            backend.close()
            (tmp_path / "events.json").write_text(json.dumps(journal.events(), ensure_ascii=False))
            (tmp_path / "frames.json").write_text(json.dumps(frames, ensure_ascii=False))


@pytest.mark.parametrize("status", ["complete", "blocked"])
@pytest.mark.parametrize("budget", [100, None])
def test_model_goal_status_stops_continuation(tmp_path, gateway, native_codex, status, budget):  # noqa: F811
    gateway.responses.put(sse({"type": "function_call", "id": "fc-goal", "call_id": "goal",
        "name": "update_goal", "arguments": json.dumps({"status": status})}))
    gateway.responses.put(answer("Goal status recorded."))
    with SessionJournal(tmp_path, "model-goal") as journal:
        backend = CodexBackend(settings(gateway, native_codex, "native"), registry([]),
                               journal, CONTEXT)
        try:
            operate(backend, {"kind": "goal_start", "objective": "Controlled status test",
                              "token_budget": budget})
            assert backend.thread_ops.goals.value["status"] == status
            assert backend.thread_ops.goals.value["tokenBudget"] == budget
            assert len(gateway.requests) == 2 and backend.runtime is None
            assert len([e for e in journal.events() if e["kind"] == "codex.turn.bound"]) == 1
        finally:
            backend.close()


def test_active_goal_recovery_pauses_before_resume(tmp_path, gateway, native_codex, monkeypatch):  # noqa: F811
    calls = []
    original = AppServer.request
    def request(self, method, params, **kwargs):
        calls.append((method, params))
        return original(self, method, params, **kwargs)
    monkeypatch.setattr(AppServer, "request", request)
    provider = settings(gateway, native_codex, "native")
    with SessionJournal(tmp_path, "abandoned") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        gateway.responses.put(answer("Existing conversation."))
        list(backend.run("Create conversation"))
        thread = backend.thread_id
        # Metadata activation on a thread without goal capability leaves durable
        # active intent, representing the state recovery must pause before load.
        backend.runtime.rpc.request("thread/goal/set", {"threadId": thread,
            "objective": "Interrupted explicit goal", "tokenBudget": 100, "status": "active"})
        backend.close()
    calls.clear()
    with SessionJournal(tmp_path, "abandoned") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            backend.resume_thread()
            assert backend.thread_ops.goals.value["status"] == "paused"
            pause = next(i for i, (m, p) in enumerate(calls)
                         if m == "thread/goal/set" and p.get("status") == "paused")
            resume = next(i for i, (m, _) in enumerate(calls) if m == "thread/resume")
            assert pause < resume and len(gateway.requests) == 1
        finally:
            backend.close()


def test_unlimited_goal_replaces_previous_finite_budget(tmp_path, gateway, native_codex):  # noqa: F811
    with SessionJournal(tmp_path, "replace-goal-budget") as journal:
        backend = CodexBackend(settings(gateway, native_codex, "native"), registry([]),
                               journal, CONTEXT)
        try:
            for budget in (100, None):
                gateway.responses.put(sse({"type": "function_call", "id": "fc-complete",
                    "call_id": "complete", "name": "update_goal",
                    "arguments": json.dumps({"status": "complete"})}))
                gateway.responses.put(answer("Completed the explicit goal."))
                operate(backend, {"kind": "goal_start", "objective": "Explicit goal",
                                  "token_budget": budget})
                assert backend.thread_ops.goals.value["status"] == "complete"
                assert backend.thread_ops.goals.value["tokenBudget"] == budget
        finally:
            backend.close()


@pytest.mark.project_memory
def test_memory_readback_is_isolated_between_projects(tmp_path, gateway, native_codex):  # noqa: F811
    provider = settings(gateway, native_codex, "native")
    enabled = {"enabled": True, "generate": False, "use": True}
    other_project = tmp_path / "other-project"
    other_project.mkdir()
    with SessionJournal(tmp_path, "memory") as journal, \
            SessionJournal(other_project, "memory") as other:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        second = CodexBackend(provider, registry([]), other, CONTEXT)
        try:
            gateway.responses.put(answer("Existing conversation."))
            list(backend.run("Create conversation"))
            gateway.responses.put(answer("Separate conversation."))
            list(second.run("Create separate conversation"))
            # An enabled memory runtime may perform a consolidation request.
            for _ in range(4):
                gateway.responses.put(answer("No memories to consolidate."))
            operate(backend, {"kind": "memory", "settings": enabled})
            assert backend.thread_ops.memory.snapshot()["effective"] == enabled
            assert backend.thread_ops.memory.snapshot()["verified"]
            assert backend.thread_ops.memory.snapshot()["scope"] == "project"
            second.thread_ops.status()
            assert second.thread_ops.memory.snapshot()["effective"] == {
                "enabled": True, "generate": True, "use": True}
            assert backend.runtime.home != second.runtime.home
            assert backend.runtime.home == journal.root / "codex"
            assert second.runtime.home == other.root / "codex"
            backend.close()
            backend.resume_thread()
            assert backend.thread_ops.memory.inspect()["effective"] == enabled
            operate(backend, {"kind": "memory", "settings": {
                "enabled": False, "generate": False, "use": False}})
            assert backend.thread_ops.memory.snapshot()["effective"]["enabled"] is False
        finally:
            backend.close()
            second.close()


def test_legacy_rollback_rejected_without_history_mutation(tmp_path, gateway, native_codex, monkeypatch):  # noqa: F811
    original = AppServer.request
    def legacy(self, method, params, **kwargs):
        if method == "thread/start":
            params = {**params, "historyMode": "legacy"}
        return original(self, method, params, **kwargs)
    monkeypatch.setattr(AppServer, "request", legacy)
    with SessionJournal(tmp_path, "legacy") as journal:
        backend = CodexBackend(settings(gateway, native_codex, "native"), registry([]),
                               journal, CONTEXT)
        try:
            gateway.responses.put(answer("Existing conversation."))
            list(backend.run("Existing conversation"))
            assert backend.history_mode == "legacy"
            events = list(backend.thread_ops.run({"kind": "rollback", "num_turns": 1,
                "history_only": True}, lambda: False, context=CONTEXT, input_id=None))
            assert backend.state.task["status"] == "failed", events
            assert "旧格式" in backend.state.task["diagnostic"]
            assert len(gateway.requests) == 1
        finally:
            backend.close()
