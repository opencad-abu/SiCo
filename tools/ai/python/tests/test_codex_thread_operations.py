"""Explicit thread controls reject stale ownership and never replay uncertainty."""

import json
from types import SimpleNamespace

import pytest
from test_agent_codex_native import CONTEXT
from test_codex_turn_inputs import backend

from sico.codex.goals import goal_value
from sico.codex.rpc import RpcError, RpcRejected
from sico.codex.thread_memory import runtime_options
from sico.codex.thread_operations import validate_action
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal
from sico.storage.native_home import selected_native_home
from sico.storage.record_integrity import require_components


def goal(**changes):
    return {**dict(threadId="thread", objective="Explicit objective", status="active",
                tokenBudget=100, tokensUsed=0, timeUsedSeconds=0, createdAt=1, updatedAt=1,
                ), **changes}


def wired(journal, monkeypatch, error=None):
    loop, calls = backend(journal), []
    from codex_thread_fixture import bind_thread
    bind_thread(loop)
    loop.cancelled = lambda: False

    def request(method, params, **kwargs):
        calls.append((method, params))
        if method == "thread/goal/get":
            return {"goal": loop.thread_ops.goals.value}
        if method == "thread/goal/set":
            return {"goal": {**loop.thread_ops.goals.value, "status": params["status"]}}
        if error:
            raise error
        return {}

    loop.runtime = SimpleNamespace(rpc=SimpleNamespace(request=request), close=lambda: None)
    loop.input_connection()
    monkeypatch.setattr(loop, "_ensure_runtime", lambda: None)
    return loop, calls


@pytest.mark.parametrize("action", [None, {}, {"kind": []}, {"kind": "shell"},
    {"kind": "fork", "cwd": "/other"},
    *[{"kind": "goal_start", "objective": "Goal", "token_budget": b}
      for b in (True, 0, -1, 1.5, 2**53)],
    {"kind": "goal_start", "objective": "Goal"},
    {"kind": "goal_start", "objective": " ", "token_budget": 10},
    {"kind": "rollback", "num_turns": 1, "history_only": False},
    {"kind": "rollback", "num_turns": True, "history_only": True},
    {"kind": "rollback_to", "before_turn_id": ""},
    {"kind": "rollback_to", "before_turn_id": " turn"},
    {"kind": "rollback_to", "before_turn_id": 1},
    {"kind": "rollback_to", "before_turn_id": "turn", "history_only": True},
    {"kind": "review", "target": {"type": []}},
    {"kind": "memory", "settings": {"enabled": False, "generate": True, "use": False}},
    {"kind": "memory", "settings": {"enabled": 1, "generate": False, "use": False}},
])
def test_invalid_operations_fail_without_dispatch(action):
    with pytest.raises(ValueError):
        validate_action(action)


def test_unlimited_goal_requires_explicit_budget_choice():
    action = {"kind": "goal_start", "objective": "User goal", "token_budget": None}
    assert validate_action(action) == action


def test_rollback_to_keeps_only_its_exact_boundary():
    action = {"kind": "rollback_to", "before_turn_id": "turn-42"}
    assert validate_action(action) == action


def test_rollback_to_dispatches_the_exact_target_without_a_count(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "rollback-to") as journal:
        loop, calls = wired(journal, monkeypatch)
        seen = []
        monkeypatch.setattr(
            "sico.codex.thread_operations.revert_to_turn",
            lambda backend, boundary: seen.append((backend, boundary)),
        )
        loop.thread_ops.start({"kind": "rollback_to", "before_turn_id": "turn-7"})
        assert seen == [(loop, "turn-7")]
        assert all(method != "thread/revert" for method, _ in calls)
        loop.close()


@pytest.mark.parametrize("changes", [{"status": []}, {"threadId": "foreign"},
    {"tokensUsed": True}, {"timeUsedSeconds": -1}, {"tokenBudget": False}, {"status": "done"}])
def test_invalid_goal_receipts_are_not_published(changes):
    with pytest.raises(ValueError):
        goal_value(goal(**changes), "thread")


@pytest.mark.parametrize("status", [
    "paused", "blocked", "usageLimited", "budgetLimited", "complete"])
def test_terminal_goal_status_survives_restore_without_activation(tmp_path, monkeypatch, status):
    with SessionJournal(tmp_path, "goal-status") as journal:
        loop, _ = wired(journal, monkeypatch)
        controls = loop.thread_ops.goals
        controls.running = True
        controls.objective = goal()["objective"]
        controls.capture(goal(status=status), source="test")
        assert not controls.continuing()
        loop.close()
    with SessionJournal(tmp_path, "goal-status") as journal:
        restored = backend(journal)
        assert restored.thread_ops.goals.value["status"] == status
        assert not restored.thread_ops.goals.running and restored.runtime is None


def test_goal_notifications_require_current_turn_and_authorized_budget(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "provenance") as journal:
        loop, _ = wired(journal, monkeypatch)
        controls = loop.thread_ops.goals
        controls.running, controls.budget = True, 100
        controls.objective = goal()["objective"]
        controls.bound("old")
        controls.bound("current")
        controls.capture(goal(), source="test")
        event = {"method": "thread/goal/updated", "params": {
            "threadId": "thread", "turnId": "future", "goal": goal(status="complete")}}
        assert not controls.notification(event)  # request() must retain a future frame.
        with pytest.raises(RpcError, match="no bound turn"):
            controls.notification(event, consuming=True)
        event["params"]["turnId"] = "old"
        assert controls.notification(event, consuming=True) and controls.continuing()
        event["params"].update(threadId="foreign", turnId="current")
        assert controls.notification(event) and controls.continuing()
        for budget in (None, 101):
            with pytest.raises(RpcError, match="authorized budget"):
                controls.capture(goal(tokenBudget=budget), source="upstream")
        for changes in ({"objective": "Different goal"}, {"createdAt": 2}):
            with pytest.raises(RpcError, match="identity or usage"):
                controls.capture(goal(**changes), source="upstream")
        event["params"].update(threadId="thread", turnId="current")
        assert controls.notification(event) and not controls.continuing()
        count = len(journal.events())
        controls.notification(event)
        assert len(journal.events()) == count
        loop.close()


def test_unlimited_goal_still_checks_identity_usage_and_turn_binding(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "unlimited") as journal:
        loop, _ = wired(journal, monkeypatch)
        controls = loop.thread_ops.goals
        controls.running, controls.budget = True, None
        controls.objective = goal()["objective"]
        controls.bound("current")
        controls.capture(goal(tokenBudget=None, tokensUsed=50), source="user")
        for changes in ({"objective": "Other"}, {"createdAt": 2}, {"tokensUsed": 49}):
            with pytest.raises(RpcError, match="identity or usage"):
                controls.capture(goal(**{"tokenBudget": None, "tokensUsed": 50, **changes}),
                    source="upstream")
        event = {"method": "thread/goal/updated", "params": {
            "threadId": "thread", "turnId": "foreign",
            "goal": goal(tokenBudget=None, tokensUsed=60)}}
        with pytest.raises(RpcError, match="no bound turn"):
            controls.notification(event, consuming=True)
        loop.close()


@pytest.mark.project_memory
def test_enabled_memory_jobs_use_the_project_model(tmp_path):
    with SessionJournal(tmp_path, "memory-model") as journal:
        options = runtime_options(journal, model="project-model")
        assert options["features.memories"] is True
        assert options["memories.extract_model"] == "project-model"
        assert options["memories.consolidation_model"] == "project-model"
        disabled = runtime_options(
            journal, {"enabled": False, "generate": False, "use": False},
            model="project-model")
        assert "memories.extract_model" not in disabled


def test_new_sessions_share_the_project_home_and_keep_legacy_homes(tmp_path):
    with SessionJournal(tmp_path, "first") as first, SessionJournal(tmp_path, "second") as second:
        project_home = first.root / "codex"
        assert selected_native_home(first.root, first.session_id) == (
            project_home, "project")
        assert selected_native_home(second.root, second.session_id) == (
            project_home, "project")
        legacy = first.directory / "codex"
        legacy.mkdir(mode=0o700)
        assert selected_native_home(first.root, first.session_id) == (
            legacy, "legacy-session")


def test_record_integrity_reads_the_project_native_home(tmp_path):
    thread_id = "00000000-0000-0000-0000-000000000001"
    with SessionJournal(tmp_path, "project-home") as journal:
        day = journal.root / "codex"
        for name in ("", "sessions", "sessions/2026", "sessions/2026/10",
                     "sessions/2026/10/20"):
            path = day / name if name else day
            path.mkdir(mode=0o700)
        rollout = day / (
            "sessions/2026/10/20/rollout-2026-10-20T10-00-00-" + thread_id + ".jsonl")
        rollout.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": thread_id}}) + "\n",
            encoding="utf-8")
        require_components(
            journal.root, journal.session_id,
            thread={"history_mode": "paginated", "thread_id": thread_id})


@pytest.mark.parametrize("boundary", ["session_id", "runtime_id", "thread_id", "connection_id",
                                      "task_id", "context", "invalidated"])
def test_operation_rejects_changed_source_before_intent(tmp_path, monkeypatch, boundary):
    with SessionJournal(tmp_path, "source") as journal:
        loop, calls = wired(journal, monkeypatch)
        controller = SessionController(loop)
        scope = controller.thread_commands.thread_scope()
        if boundary == "invalidated":
            controller.bindings.events.state = {"invalidated": {CONTEXT.target_id: True}}
        else:
            scope[boundary] = "foreign"
        with pytest.raises(ValueError, match="来源"):
            controller.thread_commands.thread_operation({"kind": "fork"}, scope=scope, request_id="request")
        assert not calls and not any(e["kind"] == "codex.operation.received"
                                     for e in journal.events())
        loop.close()


@pytest.mark.parametrize("error,status", [(RpcError("Lost receipt"), "needs_reconcile"),
                                         (RpcRejected("Rejected"), "failed")])
def test_uncertain_or_rejected_operation_is_never_replayed(tmp_path, monkeypatch, error, status):
    with SessionJournal(tmp_path, "no-replay") as journal:
        loop, calls = wired(journal, monkeypatch, error)
        controller = SessionController(loop)
        controller.thread_commands.thread_operation({"kind": "fork"}, scope=controller.thread_commands.thread_scope(),
                                    request_id="once")
        assert controller.wait(3)
        assert loop.state.task["status"] == status
        assert [m for m, _ in calls].count("thread/fork") == 1
        assert "thread/inject_items" not in [m for m, _ in calls]
        with pytest.raises(ValueError, match="不会重发"):
            controller.thread_commands.thread_operation({"kind": "fork"}, scope=controller.thread_commands.thread_scope(),
                                        request_id="once")
        loop.close()
    with SessionJournal(tmp_path, "no-replay") as journal:
        restored = SessionController(backend(journal))
        assert restored.loop.runtime is None and not restored.inbox.pending
        with pytest.raises(ValueError, match="不会重发"):
            restored.thread_commands.thread_operation({"kind": "fork"}, scope=restored.thread_commands.thread_scope(),
                                      request_id="once")


@pytest.mark.parametrize("boundary", ["cancelled", "stale", "timeout", "disconnected"])
def test_unbound_operation_turn_stops_without_replay(tmp_path, monkeypatch, boundary):
    with SessionJournal(tmp_path, "unbound") as journal:
        loop, calls = wired(journal, monkeypatch)
        controls = loop.thread_ops.goals
        controls.running, controls.budget = True, 100
        controls.objective = goal()["objective"]
        controls.capture(goal(), source="test")
        loop.cancelled = lambda: boundary == "cancelled"
        loop.stale = boundary == "stale"
        loop.provider.idle_timeout = 0 if boundary == "timeout" else 1
        def disconnected():
            raise RpcError("Disconnected")
        loop.runtime.rpc.next = disconnected
        with pytest.raises(RpcError):
            controls.await_turn()
        if boundary in {"cancelled", "stale"}:
            assert controls.value["status"] == "paused"
        assert not any(m in {"turn/start", "thread/resume"} for m, _ in calls)
        loop.close()


def test_cancel_before_dispatch_and_at_turn_completion(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "cancel") as journal:
        loop, calls = wired(journal, monkeypatch)
        list(loop.thread_ops.run({"kind": "fork"}, lambda: True, context=CONTEXT, input_id="one"))
        assert not calls and loop.state.task["status"] == "cancelled"
        loop._begin("Goal", CONTEXT, "two", "thread_operation")
        controls = loop.thread_ops.goals
        controls.running, controls.budget = True, 100
        controls.objective = goal()["objective"]
        controls.capture(goal(), source="test")
        list(loop._settle_turn("turn", "completed", lambda: True))
        assert controls.value["status"] == "paused"
        assert loop.state.task["status"] == "cancelled" and loop.runtime is None


def test_recovery_pauses_active_goal_before_loading_thread(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "recover") as journal:
        loop, calls = wired(journal, monkeypatch)
        loop.thread_ops.goals.capture(goal(), source="previous")
        loop.thread_ops.goals.before_resume(loop.runtime.rpc)
        assert [m for m, _ in calls] == ["thread/goal/get", "thread/goal/set"]
        assert loop.thread_ops.goals.value["status"] == "paused"
        assert not loop.thread_ops.goals.running
        loop.close()


@pytest.mark.project_memory
def test_memory_configuration_readback_dedup_and_project_scope(tmp_path, monkeypatch):
    enabled = {"enabled": True, "generate": False, "use": True}
    with SessionJournal(tmp_path, "memory") as journal, SessionJournal(tmp_path, "other") as other:
        loop, _ = wired(journal, monkeypatch)
        memory = loop.thread_ops.memory
        memory.configure(enabled)
        assert runtime_options(journal)["features.memories"] is True
        assert runtime_options(journal, memory.pending)["features.memories"] is True
        assert runtime_options(other) == {
            "features.memories": True,
            "memories.generate_memories": True,
            "memories.use_memories": True,
        }
        loop.runtime.home = journal.root / "codex"
        loop.runtime.home_scope = "project"
        loop.runtime.rpc.request = lambda *_a: {"config": {"features": {"memories": True},
            "memories": {"generate_memories": False, "use_memories": True}}}
        memory.inspect()
        assert memory.snapshot()["verified"]
        assert memory.snapshot()["scope"] == "project"
        assert runtime_options(journal)["features.memories"] is True
        count = len(journal.events())
        memory.inspect()
        assert len(journal.events()) == count
        loop.runtime.rpc = SimpleNamespace(request=loop.runtime.rpc.request)
        loop.input_connection()
        count = len(journal.events())
        assert not memory.snapshot()["verified"]
        memory.inspect()
        assert len(journal.events()) == count + 1
        loop.runtime.rpc.request = lambda *_a: {"config": {}}
        with pytest.raises(ValueError, match="readback"):
            memory.inspect()
        assert not memory.snapshot()["verified"]
        loop.close()


def test_empty_session_status_does_not_create_a_native_thread(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "empty") as journal:
        loop = backend(journal)
        def unexpected():
            pytest.fail("Status must not create an empty native thread")
        monkeypatch.setattr(loop, "_ensure_runtime", unexpected)
        controller = SessionController(loop)
        status = controller.thread_commands.thread_status()
        assert status["thread_id"] is None and loop.runtime is None
        for kind in ("fork", "compact", "goal_pause", "goal_clear"):
            with pytest.raises(ValueError, match="existing conversation"):
                controller.thread_commands.thread_operation({"kind": kind}, scope=controller.thread_commands.thread_scope(),
                                            request_id=kind)
        assert not any(e["kind"] == "codex.operation.received" for e in journal.events())


@pytest.mark.project_memory
def test_unconfirmed_memory_settings_are_not_committed_on_recovery(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "memory-intent") as journal:
        loop, _ = wired(journal, monkeypatch)
        loop.thread_ops.memory.configure({"enabled": True, "generate": False, "use": True})
        loop.close()
    with SessionJournal(tmp_path, "memory-intent") as journal:
        restored = backend(journal)
        assert restored.thread_ops.memory.configured is None
        assert restored.thread_ops.memory.pending is None
        assert runtime_options(journal)["memories.generate_memories"] is True


@pytest.mark.parametrize("boundary", ["cancelled", "stale"])
@pytest.mark.project_memory
def test_memory_readback_after_stop_does_not_commit_settings(tmp_path, monkeypatch, boundary):
    with SessionJournal(tmp_path, "late-memory") as journal:
        loop, _ = wired(journal, monkeypatch)
        memory = loop.thread_ops.memory
        memory.configure({"enabled": True, "generate": True, "use": True})
        def readback(*_args):
            loop.cancelled = lambda: boundary == "cancelled"
            loop.stale = boundary == "stale"
            return {"config": {"features": {"memories": True},
                               "memories": {"generate_memories": True, "use_memories": True}}}
        loop.runtime.rpc.request = readback
        with pytest.raises(RpcError, match="stopped before confirmation"):
            memory.inspect()
        assert memory.configured is None and not memory.snapshot()["verified"]
        assert runtime_options(journal)["features.memories"] is True
        loop.close()


def test_cancel_during_goal_status_prevents_history_dispatch(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "late-cancel") as journal:
        loop, calls = wired(journal, monkeypatch)
        original = loop.runtime.rpc.request
        def readback(method, params):
            result = original(method, params)
            loop.cancelled = lambda: True
            return result
        loop.runtime.rpc.request = readback
        list(loop.thread_ops.run({"kind": "fork"}, lambda: False,
                                context=CONTEXT, input_id="once"))
        assert calls == [("thread/goal/get", {"threadId": "thread"})]
        assert loop.state.task["status"] == "cancelled"
        assert loop.thread_ops.receipt["status"] == "cancelled"
