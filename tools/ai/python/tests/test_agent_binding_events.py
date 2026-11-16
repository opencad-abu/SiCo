"""Durable binding decisions, answer/deadline races and exact target ownership."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_agent_session_bindings import call, target

from sico.core.contracts import CircuitCallError
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.demo import DemoPeer
from sico.providers.mock import ScriptedProvider
from sico.service.controller import SessionController
from sico.core.session_events import EventCursor
from sico.storage.journal import SessionJournal
from sico.transport.binding_events import BindingEvents
from sico.transport.bindings import BindingRegistry
from sico.transport.bridge_client import BridgeClient
from sico.transport.broker import ContextBroker
from sico.transport.instance_bridge import InstanceBridge
from sico.transport.router_journal import record_digest


@pytest.fixture
def machine():
    registry = BindingRegistry()
    registry.add("a", target())
    now, live, busy, persisted = [100.0], [True], [False], []
    events = BindingEvents(
        registry,
        lambda context: live[0],
        lambda session, automatic: busy[0],
        persist=persisted.append,
        clock=lambda: now[0],
        wall_clock=lambda: now[0] + 1000,
    )
    return events, registry, now, live, busy, persisted


def propose(events, *, workflow=True, session="a"):
    return events.propose(
        session,
        target("sch", window="window:2"),
        task_id="task",
        origin_request_id="request",
        created_by_workflow=workflow,
        reason="Schematic created",
    )


@pytest.mark.parametrize("choice", ["add_only", "add_and_select", "decline"])
def test_resolution_is_durable_idempotent_and_does_not_dispatch(machine, choice):
    events, registry, _, _, _, persisted = machine
    proposal = propose(events)
    assert list(registry.sessions["a"].targets) == ["ciw"]
    result = events.resolve("a", proposal["event_id"], choice)
    assert result == events.resolve("a", proposal["event_id"], choice)
    assert len(persisted) == 2
    assert persisted[-1]["kind"] == "binding.resolved"
    assert result["actor"] == "user"
    assert result["source_target_id"] == "ciw"
    assert result["selected_default"] == ("sch" if choice == "add_and_select" else "ciw")
    assert ("sch" in registry.sessions["a"].targets) == (choice != "decline")
    with pytest.raises(CircuitCallError) as caught:
        events.resolve("a", proposal["event_id"], "decline" if choice != "decline" else "add_only")
    assert caught.value.code == "binding_event_changed"
    assert caught.value.data["operation_dispatched"] is False


@pytest.mark.parametrize("reason", ["foreign", "closed", "revision", "hold", "manual", "disabled"])
def test_timeout_never_takes_over_or_uses_stale_evidence(machine, reason):
    events, registry, now, live, busy, persisted = machine
    proposal = propose(events, workflow=reason != "manual")
    if reason == "foreign":
        registry.add("other", target("foreign", window="window:2", cell="Other"))
    elif reason == "closed":
        live[0] = False
    elif reason == "revision":
        registry.add("a", target("sym", window="window:3", view="symbol"))
    elif reason == "hold":
        busy[0] = True
    elif reason == "disabled":
        events.change("a", "policy", auto_bind=False, timeout_seconds=30)
    now[0] += 30
    events.tick()
    assert "sch" not in registry.sessions["a"].targets
    result = persisted[-1]
    assert result["kind"] == "binding.invalidated"
    assert result["payload"]["actor"] == (
        "host" if reason in {"closed", "revision", "disabled"} else "timeout_policy"
    )
    assert result["payload"]["error"]["data"]["operation_dispatched"] is False
    if reason == "foreign":
        assert result["payload"]["error"]["data"]["owner_session_id"] == "other"
    with pytest.raises(CircuitCallError):
        events.resolve("a", proposal["event_id"], "add_and_select")


def test_late_answer_runs_timeout_policy_once_even_without_timer_tick(machine):
    events, registry, now, _, _, persisted = machine
    proposal = propose(events)
    now[0] += 30
    with pytest.raises(CircuitCallError) as caught:
        events.resolve("a", proposal["event_id"], "decline")
    assert caught.value.code == "binding_event_expired"
    assert registry.sessions["a"].default_target_id == "sch"
    assert persisted[-1]["payload"]["actor"] == "timeout_policy"
    events.tick()
    assert len(persisted) == 2


def test_resolution_fsync_failure_leaves_proposal_and_ownership_unchanged(machine):
    events, registry, _, _, _, _ = machine
    proposal = propose(events)

    def fail(event):
        assert registry.sessions["a"].default_target_id == "ciw"
        assert "sch" not in registry.sessions["a"].targets
        raise OSError("disk full")

    events.persist = fail
    with pytest.raises(OSError, match="disk full"):
        events.resolve("a", proposal["event_id"], "add_and_select")
    assert events.proposals[proposal["event_id"]]["status"] == "proposed"
    assert len(events.events) == 1
    assert "sch" not in registry.sessions["a"].targets


def test_foreign_answer_and_early_timeout_are_rejected(machine):
    events, _, _, _, _, persisted = machine
    proposal = propose(events)
    with pytest.raises(ValueError, match="belong"):
        events.resolve("other", proposal["event_id"], "add_only")
    with pytest.raises(CircuitCallError, match="elapsed"):
        events.resolve("a", proposal["event_id"], "add_and_select", actor="timeout_policy")
    assert len(persisted) == 1


@pytest.mark.parametrize("at_deadline", [False, True])
def test_user_timeout_race_has_one_durable_winner(at_deadline):
    ciw, sch = target(), target("sch", window="window:2")
    with ContextBroker(2) as broker:
        peer = DemoPeer(broker, ciw)
        try:
            broker.register_session("a", ciw)
            broker.register_target(sch)
            now = [0]
            broker.binding_events.clock = lambda: now[0]
            proposal = broker.propose_binding(
                "a", sch, created_by_workflow=True, task_id="task", origin_request_id="request"
            )
            now[0] = 30 if at_deadline else 29
            barrier = threading.Barrier(2)

            def answer():
                barrier.wait(3)
                try:
                    return broker.resolve_binding("a", proposal["event_id"], "decline")
                except CircuitCallError as error:
                    return error.code

            def timeout():
                barrier.wait(3)
                broker.poll_bindings()

            with ThreadPoolExecutor(2) as pool:
                answered, timed = pool.submit(answer), pool.submit(timeout)
                answered.result(3)
                timed.result(3)
            rows = broker.binding_events.events
            assert [row["kind"] for row in rows] == ["binding.proposed", "binding.resolved"]
            assert rows[-1]["payload"]["actor"] == ("timeout_policy" if at_deadline else "user")
        finally:
            peer.close()


def test_explicit_release_is_idle_only_and_other_alias_retains_ownership(machine):
    events, registry, _, _, busy, _ = machine
    sch = target("sch", window="window:2")
    alias = target("alias", window="window:2")
    registry.add("a", sch)
    registry.add("a", alias)
    busy[0] = True
    with pytest.raises(CircuitCallError) as caught:
        events.change("a", "release", context=sch)
    assert caught.value.code == "binding_busy"
    busy[0] = False
    events.change("a", "release", context=sch)
    with pytest.raises(CircuitCallError):
        registry.add("other", sch)
    events.change("a", "release", context=alias)
    registry.add("other", sch)
    assert "sch" in registry.sessions["other"].targets


def test_bridge_authentication_provenance_and_durable_receipts(tmp_path):
    ciw, sch = target(), target("sch", window="window:2")
    with InstanceBridge(tmp_path, ciw) as bridge:
        peer = DemoPeer(bridge.broker, ciw)
        try:
            with (
                BridgeClient.discover(tmp_path, ciw) as a,
                BridgeClient.discover(tmp_path, ciw) as b,
            ):
                a.register_session("a", ciw)
                b.register_session("b", ciw)
                a.register_target(sch)
                proposal = a._call(
                    "propose_binding", session_id="a", context=sch, created_by_workflow=True
                )
                assert proposal["created_by_workflow"] is False
                assert proposal["timeout_policy"] == "expire"
                with pytest.raises(Exception, match="desktop"):
                    b.resolve_binding("a", proposal["event_id"], "add_only")
                result = a.resolve_binding("a", proposal["event_id"], "add_and_select")
                assert call(a, "a", sch)["valid"]
                assert result == a.resolve_binding("a", proposal["event_id"], "add_and_select")
                records = [
                    json.loads(line)
                    for line in bridge.binding_journal.path.read_text().splitlines()
                ]
                assert len(records) == 2
                assert all(row["sha256"] == record_digest(row["event"]) for row in records)
                assert bridge.binding_journal.path.stat().st_mode & 0o077 == 0
                a.select_session_target("a", ciw)
                a.release_binding("a", sch)
                b.register_target(sch)
                other = b.propose_binding("b", sch)
                b.resolve_binding("b", other["event_id"], "add_only")
                with pytest.raises(CircuitCallError):
                    call(a, "a", sch)
        finally:
            peer.close()


def test_host_timeout_reaches_desktop_and_session_log_without_active_task(tmp_path):
    ciw, sch = target(), target("sch", window="window:2")
    with InstanceBridge(tmp_path, ciw) as bridge, SessionJournal(tmp_path, "a") as journal:
        peer = DemoPeer(bridge.broker, ciw)
        try:
            with BridgeClient.discover(tmp_path, ciw) as client:
                controller = SessionController(
                    PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, ciw)
                )
                controller.bindings.attach_targets(client)
                now = [0]
                bridge.broker.binding_events.clock = lambda: now[0]
                bridge.publish_binding(
                    "a", sch, task_id="task", origin_request_id="request", created_by_workflow=True
                )
                controller.bindings.poll_binding_events()
                assert controller.updates.read_updates()["snapshot"]["binding_state"]["proposals"]
                now[0] = 30
                bridge.broker.poll_bindings()
                controller.bindings.poll_binding_events()
                assert controller.bindings.events.default_context == sch
                assert controller.current == ciw
                assert call(client, "a", sch)["valid"]
                batch = controller.updates.read_updates()
                cursor = EventCursor("a", controller.runtime_id, ciw)
                assert len(cursor.consume(batch)) == 2
                assert not journal.state.task
                assert [row["kind"] for row in journal.events()] == [
                    "binding.proposed",
                    "binding.resolved",
                ]
                controller.paused = True
                controller.submit("New schematic task")
                assert controller.inbox.pending[-1]["message"]["context"] == sch.record()
                controller.close()
        finally:
            peer.close()


def test_default_change_preserves_queued_context_and_release_rejects_queue(tmp_path):
    ciw, sch = target(), target("sch", window="window:2")
    with ContextBroker(2) as broker, SessionJournal(tmp_path, "a") as journal:
        peer = DemoPeer(broker, ciw)
        controller = SessionController(
            PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, ciw)
        )
        try:
            controller.bindings.attach_targets(broker)
            controller.paused = True
            controller.submit("Captured CIW task")
            proposal = controller.bindings.propose_binding(sch)
            controller.bindings.resolve_binding(proposal["event_id"], "add_and_select")
            controller.submit("Captured schematic task")
            assert [row["message"]["context"]["target_id"] for row in controller.inbox.pending] == [
                "ciw",
                "sch",
            ]
            assert controller.loop.state.context == ciw
            with pytest.raises(CircuitCallError) as caught:
                controller.bindings.release_binding("ciw")
            assert caught.value.code == "binding_busy"
        finally:
            controller.close()
            peer.close()


def test_changed_generation_invalidates_one_proposal_without_cancelled_task(machine):
    events, registry, now, _, _, _ = machine
    proposal = propose(events)
    registry.remove("a")
    now[0] += 30
    events.tick()
    result = events.proposals[proposal["event_id"]]
    assert result["error"]["code"] == "binding_event_changed"
    assert result["error"]["data"]["current_revision"] is None
    assert not any(event["kind"] == "task.cancelled" for event in events.events)


def test_same_session_id_reopened_does_not_revive_old_proposal(machine):
    events, registry, now, _, _, _ = machine
    proposal = propose(events)
    original = registry.sessions["a"].binding_id
    registry.remove("a")
    registry.add("a", target())
    assert registry.sessions["a"].binding_id != original
    assert registry.sessions["a"].revision == proposal["expected_revision"]
    with pytest.raises(CircuitCallError) as caught:
        events.resolve("a", proposal["event_id"], "add_only")
    assert caught.value.code == "binding_event_changed"
    now[0] += 30
    events.tick()
    assert "sch" not in registry.sessions["a"].targets


def test_close_persists_invalidation_before_session_is_released(tmp_path):
    ciw, sch = target(), target("sch", window="window:2")
    with InstanceBridge(tmp_path, ciw) as bridge:
        peer = DemoPeer(bridge.broker, ciw)
        try:
            with BridgeClient.discover(tmp_path, ciw) as client:
                client.register_session("a", ciw)
                proposal = bridge.publish_binding(
                    "a", sch, created_by_workflow=True, task_id="task", origin_request_id="request"
                )
                client.release_session("a")
                result = bridge.broker.binding_events.proposals[proposal["event_id"]]
                assert result["status"] == "invalidated"
                assert result["actor"] == "host"
                assert "a" not in bridge.broker.bridge_snapshot()["sessions"]
                saved = json.loads(bridge.binding_journal.path.read_text().splitlines()[-1])
                assert saved["event"]["kind"] == "binding.invalidated"
        finally:
            peer.close()


def test_second_proposal_is_invalidated_after_first_changes_revision(machine):
    events, registry, _, _, _, _ = machine
    first = propose(events)
    second = events.propose("a", target("sym", window="window:3", view="symbol"))
    events.resolve("a", first["event_id"], "add_and_select")
    events.tick()
    assert events.proposals[second["event_id"]]["error"]["code"] == "binding_event_changed"
    assert set(registry.sessions["a"].targets) == {"ciw", "sch"}


def test_task_dequeue_cannot_replace_user_default_even_before_desktop_poll(machine):
    events, registry, _, _, _, persisted = machine
    proposal = propose(events)
    events.resolve("a", proposal["event_id"], "add_and_select")
    snapshot = events.change("a", "select", context=target(), actor="task")
    assert snapshot["default_explicit"] is True
    assert snapshot["default_target_id"] == "sch"
    assert registry.sessions["a"].default_target_id == "sch"
    assert len(persisted) == 2


@pytest.mark.parametrize("resource", [False, True])
def test_submission_reservation_during_release_is_rejected(tmp_path, monkeypatch, resource):
    ciw, sch = target(), target("sch", window="window:2")
    with ContextBroker(2) as broker, SessionJournal(tmp_path, "a") as journal:
        peer = DemoPeer(broker, ciw)
        controller = SessionController(
            PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, ciw)
        )
        entered, release = threading.Event(), threading.Event()
        try:
            controller.bindings.attach_targets(broker)
            proposal = controller.bindings.propose_binding(sch)
            controller.bindings.resolve_binding(proposal["event_id"], "add_only")
            controller.current = sch
            controller.bindings.displayed = sch
            if resource:
                broker.binding_events.reserve("a", sch, "resource")
                controller.bindings.poll_binding_events()
            method = "release_binding_resource" if resource else "release_binding"
            original = getattr(broker, method)

            def slow(*args):
                entered.set()
                assert release.wait(3)
                return original(*args)

            monkeypatch.setattr(broker, method, slow)
            with ThreadPoolExecutor(1) as pool:
                released = pool.submit(
                    getattr(controller.bindings, method), "resource_0" if resource else "sch"
                )
                assert entered.wait(3)
                try:
                    with pytest.raises(ValueError, match="正在释放"):
                        controller.bindings.reserve_submission(sch)
                    assert not released.done()
                finally:
                    release.set()
                released.result(3)
            assert controller.current == (sch if resource else ciw)
            assert ("sch" in controller.bindings.events.targets) is resource
            assert not controller.bindings._submissions and controller.bindings._resource_release is None
        finally:
            release.set()
            controller.close()
            peer.close()


def test_session_mirror_failure_keeps_bridge_receipt_and_cursor(tmp_path, monkeypatch):
    ciw, sch = target(), target("sch", window="window:2")
    with ContextBroker(2) as broker, SessionJournal(tmp_path, "a") as journal:
        peer = DemoPeer(broker, ciw)
        controller = SessionController(
            PythonBackend(ScriptedProvider([]), ToolRegistry(), journal, ciw)
        )
        try:
            controller.bindings.attach_targets(broker)
            proposal = controller.bindings.propose_binding(sch)
            cursor = controller.bindings.events.cursor
            original = journal.append_binding

            def unavailable(*args):
                raise OSError("Session mirror unavailable")

            monkeypatch.setattr(journal, "append_binding", unavailable)
            broker.resolve_binding("a", proposal["event_id"], "add_and_select")
            controller.bindings.poll_binding_events()
            assert controller.bindings.events.cursor == cursor
            assert controller.updates.read_updates()["snapshot"]["binding_error"]
            assert broker.binding_snapshot("a")["default_target_id"] == "sch"
            monkeypatch.setattr(journal, "append_binding", original)
            controller.bindings.poll_binding_events()
            controller.bindings.poll_binding_events()
            assert controller.bindings.events.default_context == sch
            assert [row["kind"] for row in journal.events()] == [
                "binding.proposed",
                "binding.resolved",
            ]
            assert not controller.updates.read_updates()["snapshot"]["binding_error"]
        finally:
            controller.close()
            peer.close()
