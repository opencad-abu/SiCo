"""Session ownership, queue/cancel ordering, persistence and stale-event rejection."""

from __future__ import annotations

from sico.service.frontend_session import SessionToken

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from agent_sessions_harness import DesktopSessions

from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext, ModelTurn, NeedsReconcile, TextDelta
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.demo import DemoPeer
from sico.providers.mock import ContextDemoProvider, ScriptedProvider
from sico.service.controller import SessionController
from sico.core.session_events import EventCursor
from sico.storage.journal import SessionJournal
from sico.transport.broker import ContextBroker
from sico.transport.methods import QueryUnavailable
from sico.transport.router import SkillInstanceRouter


def context(key="bound"):
    return BoundContext("instance", "generation", key, {"valid": True, "window": key})


def message(key):
    return {"kind": "submit", "id": key, "text": "Read " + key, "context": context(key).record()}


def wait(condition):
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert condition()


def test_headless_queue_cancellation_preserves_targets_and_pauses_next(tmp_path):
    entered, release = threading.Event(), threading.Event()
    seen, released = [], []

    class Broker:
        def register_target(self, ctx):
            pass

        def release_target(self, ctx):
            released.append(ctx.target_id)

        def get_context(self, ctx):
            seen.append(ctx.target_id)
            if ctx.target_id == "q1":
                entered.set()
                assert release.wait(5)
            return ctx.snapshot

    with SessionJournal(tmp_path, "headless") as journal:
        broker = Broker()
        loop = PythonBackend(ContextDemoProvider(), context_tools(broker), journal, context())
        controller = SessionController(loop)
        controller.bindings.attach_targets(broker)
        try:
            controller.accept(message("q1"))
            assert entered.wait(5)
            controller.accept(message("q2"))
            assert not controller.accept(message("q2"))
            manual = controller.submit("Follow up on q1")
            assert controller.current.target_id == "q1"
            assert controller.updates.read_updates()["snapshot"]["pending"] == 2
            controller.cancel()
            assert controller.updates.read_updates()["snapshot"]["cancelling"]
            release.set()
            assert controller.wait(5)
            assert seen == ["q1"]
            assert controller.updates.read_updates()["snapshot"]["paused"]
            assert loop.state.task["status"] == "cancelled"
            assert "q1" not in released
            controller.resume()
            assert controller.wait(5)
            assert seen == ["q1", "q2", "q1"]
            starts = [e for e in journal.events() if e["kind"] == "task.started"]
            assert [e["payload"]["input_id"] for e in starts] == ["q1", "q2", manual]
            assert len([e for e in journal.events() if e["kind"] == "tool.started"]) == 3
            assert released == ["q2"]
        finally:
            release.set()
            controller.close()
            assert controller.wait(5)


def test_disconnect_blocks_queue_until_explicit_reconcile_and_resume(tmp_path):
    class Broker:
        def get_context(self, ctx):
            raise NeedsReconcile("Test relay disconnected")

    with SessionJournal(tmp_path, "disconnect") as journal:
        loop = PythonBackend(ContextDemoProvider(), context_tools(Broker()), journal, context())
        controller = SessionController(loop)
        controller.cancel()  # Queue two requests before execution starts.
        first, second = controller.submit("Read first"), controller.submit("Read second")
        controller.resume()
        assert controller.wait(5)
        state = controller.updates.read_updates()["snapshot"]
        assert state["task"]["status"] == "needs_reconcile" and state["pending"] == 1
        with pytest.raises(ValueError):
            controller.resume()
        with pytest.raises(ValueError, match="未核对"):
            controller.acknowledge_interrupted()
        assert controller.updates.read_updates()["snapshot"]["paused"]
        assert [r["id"] for r in state["requests"]] == [first, second]
        controller.close()
        assert (
            json.loads((controller.inbox.directory / (second + ".json")).read_text())["status"]
            == "not_started"
        )


def test_reopen_keeps_interrupted_input_evidence_without_execution(tmp_path):
    with SessionJournal(tmp_path, "recover") as journal:
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, context())
        controller = SessionController(loop)
        controller.cancel()
        queued = controller.submit("Only accepted; never sent")
        # Simulate a killed desktop: accepted durable queue survives without orderly close.
    with SessionJournal(tmp_path, "recover") as journal:
        provider = ScriptedProvider([])
        controller = SessionController(PythonBackend(provider, ToolRegistry(), journal, context()))
        state = controller.updates.read_updates()["snapshot"]
        assert state["pending"] == 0 and not state["busy"]
        assert state["requests"][0]["id"] == queued
        assert state["requests"][0]["status"] == "not_started"
        assert journal.events() == []
        controller.close()


def test_event_cursor_rejects_foreign_duplicate_and_late_task_events(tmp_path):
    with SessionJournal(tmp_path, "events") as journal:
        controller = SessionController(
            PythonBackend(
                ScriptedProvider([[TextDelta("one"), ModelTurn("one")], [ModelTurn("")]]),
                ToolRegistry(),
                journal,
                context(),
            )
        )
        controller.submit("first")
        assert controller.wait(5)
        controller.submit("second")
        assert controller.wait(5)
        batch = controller.updates.read_updates()
        cursor = EventCursor(controller.session_id, controller.runtime_id, context())
        for field in ("session_id", "runtime_id", "instance_id", "generation"):
            with pytest.raises(ValueError):
                cursor.consume({**batch, field: "foreign"})
            assert cursor.sequence == 0
        expected = len(batch["events"])
        assert len(cursor.consume(batch)) == expected
        with pytest.raises(ValueError):
            cursor.consume(batch)
        stale = {**batch["events"][1], "sequence": expected + 1}
        with pytest.raises(ValueError, match="Stale"):
            cursor.consume({**batch, "events": [stale]})
        assert cursor.sequence == expected
        controller.close()


def test_event_cursor_accepts_router_status_with_current_task_identity(tmp_path):
    cursor = EventCursor("router-events", "runtime", context())
    events = [
        {
            "session_id": "router-events", "sequence": 1, "task_id": "task-1",
            "kind": "task.started", "payload": {"context": context().record()},
        },
        {
            "session_id": "router-events", "sequence": 2, "task_id": "task-1",
            "kind": "router.status", "payload": {
                "router_id": "router-1", "request_id": "request-1",
                "instance_id": "instance", "generation": "generation",
                "execution_mode": "foreground", "state": "queued",
            },
        },
    ]
    batch = {
        "contract": "agent_session.v1", "session_id": "router-events",
        "runtime_id": "runtime", "instance_id": "instance", "generation": "generation",
        "events": events,
    }
    assert len(cursor.consume(batch)) == len(events)


def test_worker_cancels_queued_router_request_and_persists_owner_event(tmp_path):
    with ContextBroker(2) as broker, SessionJournal(tmp_path, "router-owner") as journal:
        peer = DemoPeer(broker)
        bound = peer.context
        router = broker.registry.get(bound.instance_id, bound.generation)
        provider = ScriptedProvider([])
        loop = PythonBackend(provider, context_tools(broker), journal, bound)
        controller = SessionController(loop)
        controller.bindings.attach_targets(broker)
        sessions = DesktopSessions(controller, broker=broker)
        entered, release = threading.Event(), threading.Event()

        def occupied(_request_id):
            entered.set()
            assert release.wait(5)
            return {"ok": True}

        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(router.execute, "read", occupied, session_id="other-session")
            try:
                assert entered.wait(2)
                assert sessions.worker.command(SessionToken(controller.session_id, controller.runtime_id), "initialize_context").result(2)
                wait(lambda: any(event["kind"] == "router.status"
                                 and event["payload"]["state"] == "queued"
                                 for event in journal.events()))
                queued = next(event["payload"] for event in journal.events()
                              if event["kind"] == "router.status"
                              and event["payload"]["state"] == "queued")
                wait(lambda: bool((controller.updates.read_updates()["snapshot"].get("router") or {})
                                  .get("requests")))
                state = controller.updates.read_updates()["snapshot"]
                assert state["busy"] and not first.done()
                assert state["router"]["owner"]["session_id"] == "other-session"
                assert len(state["router"]["requests"]) == 1
                request = state["router"]["requests"][0]
                assert request["request_id"] == queued["request_id"]
                assert request["task_id"] == state["task"]["id"]
                assert request["state"] == "queued" and request["queue_position"] == 1
                receipt = sessions.worker.command(
                    SessionToken(controller.session_id, controller.runtime_id), "cancel_router_request", queued["request_id"], context=bound,
                )
                assert receipt.result(2) == "cancel_requested"
                assert controller.wait(2)
                assert not first.done()
                wait(lambda: controller.updates.read_updates()["snapshot"]["router"]["requests"] == [])
                events = EventCursor(controller.session_id, controller.runtime_id, bound).consume(
                    controller.updates.read_updates(),
                )
                statuses = [event for event in events if event["kind"] == "router.status"]
                assert [event["payload"]["state"] for event in statuses] == [
                    "accepted", "queued", "cancelled_before_start",
                ]
                terminal = statuses[-1]
                assert terminal["payload"]["request_id"] == queued["request_id"]
                assert terminal["payload"]["session_id"] == controller.session_id
                assert terminal["task_id"] == terminal["payload"]["task_id"]
                assert terminal["payload"]["tool_call_id"] == queued["tool_call_id"]
                assert router.snapshot()["queued"] == 0
                assert not provider.requests
                tool = next(event for event in events if event["kind"] == "tool.finished")
                assert tool["payload"]["result"]["status"] == "cancelled_before_start"
                stale = BoundContext(bound.instance_id, bound.generation, "stale", bound.snapshot)
                with pytest.raises(ValueError, match="target has changed"):
                    sessions.worker.command(SessionToken(controller.session_id, controller.runtime_id), "cancel_router_request",
                                            queued["request_id"], context=stale).result(2)
            finally:
                release.set()
                first.result(2)
                sessions.close()
                assert sessions.wait(5)
                peer.close()


def test_router_snapshot_recovers_after_late_reply_without_new_session_events(tmp_path):
    with ContextBroker(2) as broker, SessionJournal(tmp_path, "router-status") as journal:
        peer = DemoPeer(broker)
        bound = peer.context
        loop = PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, bound)
        controller = SessionController(loop)
        controller.bindings.attach_targets(broker)
        sessions = DesktopSessions(controller, broker=broker)
        router = broker.registry.get(bound.instance_id, bound.generation)

        def timeout(_request_id):
            raise QueryUnavailable("skill_timeout", "Unknown result")

        def status_is(name):
            status = controller.updates.read_updates()["snapshot"]["router"]
            return status is not None and status["state"] == name

        try:
            wait(lambda: status_is("idle"))
            version = controller.updates.read_updates()["snapshot"]["version"]
            with pytest.raises(QueryUnavailable):
                router.execute("read", timeout, session_id=controller.session_id,
                               target_id=bound.target_id, task_id="timed-out-task")
            wait(lambda: status_is("blocked_unknown"))
            state = controller.updates.read_updates(since_version=version)["snapshot"]
            request = state["router"]["requests"][0]
            assert request["task_id"] == "timed-out-task"
            assert request["state"] == "timed_out_unknown"
            assert state["router"]["owner"]["request_id"] == request["request_id"]
            state["router"]["owner"]["session_id"] = "mutated"
            assert controller.updates.read_updates()["snapshot"]["router"]["owner"]["session_id"] == (
                controller.session_id
            )
            version = state["version"]
            controller.router.poll_router_status()
            assert controller.updates.read_updates(since_version=version)["snapshot"] is None
            router.clear_unknown("unrelated")
            controller.router.poll_router_status()
            assert controller.updates.read_updates(since_version=version)["snapshot"] is None
            router.clear_unknown(request["request_id"])
            wait(lambda: status_is("idle"))
            state = controller.updates.read_updates(since_version=version)["snapshot"]
            assert state["router"]["requests"] == [] and state["router"]["owner"] is None
            assert journal.events() == []
        finally:
            sessions.close()
            assert sessions.wait(5)
            peer.close()


def test_router_snapshot_discards_inflight_old_target_and_hides_cached_old_context(tmp_path):
    entered, release = threading.Event(), threading.Event()
    router = SkillInstanceRouter("instance", "generation")

    class Broker:
        def router_snapshot(self, ctx):
            entered.set()
            assert release.wait(5)
            return router.snapshot()

    with SessionJournal(tmp_path, "router-switch") as journal:
        controller = SessionController(
            PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, context())
        )
        controller.bindings.attach_targets(Broker())
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(controller.router.poll_router_status)
            try:
                assert entered.wait(2)
                with controller._lock:
                    controller.current = context("new-target")
            finally:
                release.set()
            pending.result(2)
        assert controller.updates.read_updates()["snapshot"]["router"] is None
        controller.router.poll_router_status()
        assert controller.updates.read_updates()["snapshot"]["router"]["target_id"] == "new-target"
        controller.current = context("third-target")
        assert controller.updates.read_updates()["snapshot"]["router"] is None


def test_persistence_failure_stops_queue_and_does_not_claim_success(tmp_path, monkeypatch):
    with SessionJournal(tmp_path, "disk") as journal:
        controller = SessionController(
            PythonBackend(
                ScriptedProvider([[ModelTurn("")]]),
                ToolRegistry(),
                journal,
                context(),
            )
        )
        controller.cancel()
        request_id = controller.submit("accepted before disk error")
        monkeypatch.setattr(
            controller.inbox, "_save", lambda record: (_ for _ in ()).throw(OSError())
        )
        controller.resume()
        assert controller.wait(5)
        state = controller.updates.read_updates()["snapshot"]
        assert state["fault"] and state["paused"]
        assert state["requests"][0]["status"] == "needs_reconcile"
        record = json.loads((controller.inbox.directory / (request_id + ".json")).read_text())
        assert record["status"] == "queued" and journal.events() == []
        with pytest.raises(ValueError):
            controller.submit("must not execute")
        controller.close()


def test_chat_captures_displayed_target_while_queue_advances(tmp_path):
    entered, release = threading.Event(), threading.Event()
    seen, released = [], []

    class Broker:
        def register_target(self, ctx):
            pass

        def release_target(self, ctx):
            released.append(ctx.target_id)

        def get_context(self, ctx):
            seen.append(ctx.target_id)
            if ctx.target_id == "q2":
                entered.set()
                assert release.wait(5)
            return ctx.snapshot

    with SessionJournal(tmp_path, "display-target") as journal:
        controller = SessionController(
            PythonBackend(ContextDemoProvider(), context_tools(Broker()), journal, context())
        )
        controller.bindings.attach_targets(Broker())
        try:
            controller.accept(message("q1"))
            assert controller.wait(5)
            controller.updates.read_updates()
            displayed = context("q1")
            controller.accept(message("q2"))
            assert entered.wait(5)
            assert "q1" not in released
            manual = controller.submit("Read displayed source", context=displayed)
            controller.updates.read_updates()  # GUI catches up after accepting the old displayed source.
            assert "q1" not in released  # Kept for the queued manual request.
            release.set()
            assert controller.wait(5)
            starts = [e for e in journal.events() if e["kind"] == "task.started"]
            assert starts[-1]["payload"]["input_id"] == manual
            assert starts[-1]["payload"]["context"]["target_id"] == "q1"
            assert seen == ["q1", "q2", "q1"]
        finally:
            release.set()
            controller.close()
            assert controller.wait(5)


def test_batch_replay_is_bounded_and_idle_poll_has_no_repeated_snapshot(tmp_path):
    with SessionJournal(tmp_path, "batch") as journal:
        deltas = [TextDelta("x") for _ in range(205)]
        controller = SessionController(
            PythonBackend(
                ScriptedProvider([deltas + [ModelTurn("x" * 205)]]),
                ToolRegistry(),
                journal,
                context(),
            )
        )
        controller.submit("Long stream")
        assert controller.wait(5)
        cursor = EventCursor(controller.session_id, controller.runtime_id, context())
        counts = []
        while True:
            batch = controller.updates.read_updates(cursor.sequence)
            counts.append(len(cursor.consume(batch)))
            if batch["snapshot"] is not None:
                break
        assert counts == [100, 100, 9]
        version = batch["snapshot"]["version"]
        idle = controller.updates.read_updates(cursor.sequence, since_version=version)
        assert idle["events"] == [] and idle["snapshot"] is None
        controller.close()


def test_activity_is_available_in_live_snapshot_and_cleared_on_completion(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class Provider:
        def stream(self, *args):
            entered.set()
            assert release.wait(3)
            yield TextDelta("done")
            yield ModelTurn("done")

    with SessionJournal(tmp_path, "activity") as journal:
        controller = SessionController(PythonBackend(Provider(), ToolRegistry(), journal, context()))
        controller.submit("wait")
        assert entered.wait(3)
        snapshot = controller.updates.read_updates()["snapshot"]
        assert snapshot["task"]["activity"]["phase"] == "waiting_model"
        release.set()
        assert controller.wait(3)
        assert "activity" not in controller.updates.read_updates()["snapshot"]["task"]
        controller.close()


def test_text_attachment_is_durable_and_reaches_agent_loop(tmp_path):
    payload = "完整剪贴板资料\n" + "x" * 16001
    context_value = context()
    with SessionJournal(tmp_path, "attachment-controller") as journal:
        provider = ScriptedProvider([[TextDelta("完成"), ModelTurn("完成")]])
        loop = PythonBackend(provider, ToolRegistry(), journal, context_value)
        controller = SessionController(loop)
        request_id = controller.submit("请分析附件", attachment_text=payload)
        assert controller.wait(5)
        record = json.loads((controller.inbox.directory / (request_id + ".json")).read_text())
        attachments = record["message"]["attachments"]
        assert len(attachments) == 1
        manifest = attachments[0]
        assert journal.artifact_text(manifest["path"], manifest["sha256"]) == payload
        started = next(event for event in journal.events() if event["kind"] == "task.started")
        assert started["payload"]["attachments"] == attachments
        assert manifest["path"] in provider.requests[0]["messages"][0]["content"]
        assert loop.state.task["attachments"] == attachments
        controller.close()
