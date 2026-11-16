"""Multi-target sessions, exclusive editor ownership and immutable dispatch."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_agent_bridge import connect_peer, reply

from sico.core.contracts import BoundContext, CircuitCallError, NeedsReconcile
from sico.demo import DemoPeer
from sico.transport.bindings import BindingRegistry
from sico.transport.bridge_client import BridgeClient
from sico.transport.broker import ContextBroker, skill_call_context
from sico.transport.framing import VERSION
from sico.transport.instance_bridge import InstanceBridge


def target(
    key="ciw",
    *,
    window=None,
    cell="Amp",
    view="schematic",
    library="Work",
    library_path=None,
    generation="g",
):
    snapshot = {
        "source": "simulated",
        "valid": True,
        "cwd": "/project",
        "capabilities": ["get_context", "circuit_call"],
    }
    if window:
        snapshot.update(
            window=window,
            window_ref="window:" + key,
            cellview={"lib": library, "cell": cell, "view_name": view},
        )
    if library_path:
        snapshot["library_path"] = str(library_path)
    return BoundContext("instance", generation, key, snapshot)


def call(broker, session, context, *, progress=None):
    with skill_call_context(session_id=session, progress=progress):
        return broker.get_context(context)


def test_multi_target_default_and_ciws_are_independent():
    registry = BindingRegistry()
    ciw = target()
    sch = target("sch", window="window:2")
    sym = target("sym", window="window:3", view="symbol")
    registry.add("a", ciw)
    registry.add("b", ciw)
    registry.add("a", sch)
    record = registry.add("a", sym)
    assert record.anchor == ciw and record.default_target_id == "ciw"
    assert set(record.targets) == {"ciw", "sch", "sym"}
    assert registry.add("a", sch) is record
    assert registry.select("a", sym).default_target_id == "sym"
    assert registry.sessions["b"].default_target_id == "ciw"
    snapshot = record.snapshot()
    snapshot["targets"][0]["snapshot"]["cwd"] = "/other"
    assert record.anchor.snapshot["cwd"] == "/project"
    with pytest.raises(ValueError, match="generation"):
        registry.add("a", target(generation="new"))


@pytest.mark.parametrize(
    "other",
    [
        target("same-window", window="window:2", cell="Different"),
        target("same-cellview", window="window:9"),
        target("same-ade", window="window:7", view="maestro"),
    ],
)
def test_window_and_design_ownership_are_atomic(other):
    registry = BindingRegistry()
    owned = target(
        "sch", window="window:2", view=other.snapshot["cellview"]["view_name"]
    )
    registry.add("a", owned)
    with pytest.raises(CircuitCallError) as caught:
        registry.add("b", other)
    assert caught.value.code == "binding_conflict"
    assert caught.value.data["owner_session_id"] == "a"
    assert caught.value.data["operation_dispatched"] is False
    assert "b" not in registry.sessions
    registry.remove("a")
    assert registry.add("b", other).anchor == other


def test_library_path_aliases_and_ade_sessions_conflict(tmp_path):
    actual = tmp_path / "library"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    registry = BindingRegistry()
    registry.add("a", target("sch", window="window:2", library_path=actual))
    with pytest.raises(CircuitCallError, match="owned"):
        registry.add("b", target("other", window="window:8", library="Alias", library_path=alias))
    first = target("ade", window="swindow:5", view="maestro")
    first = BoundContext(
        first.instance_id,
        first.generation,
        first.target_id,
        {**first.snapshot, "ade_session": "fnxSession1"},
    )
    second = BoundContext(
        first.instance_id,
        first.generation,
        "ade-other",
        {**first.snapshot, "window": "swindow:6", "cellview": None},
    )
    registry.add("ade-a", first)
    with pytest.raises(CircuitCallError, match="owned"):
        registry.add("ade-b", second)


def test_concurrent_ownership_claim_has_exactly_one_winner():
    sch = target("sch", window="window:2")
    with ContextBroker(2) as broker:
        peer = DemoPeer(broker, sch)
        barrier = threading.Barrier(2)

        def claim(session_id):
            barrier.wait(3)
            try:
                broker.register_session(session_id, sch)
                return session_id
            except CircuitCallError as exc:
                assert exc.code == "binding_conflict"
                return None

        try:
            with ThreadPoolExecutor(2) as pool:
                futures = [pool.submit(claim, name) for name in ("a", "b")]
                winners = [future.result(3) for future in futures]
            assert sum(winner is not None for winner in winners) == 1
            assert len(broker.bridge_snapshot()["sessions"]) == 1
        finally:
            peer.close()


def test_new_generation_retires_all_targets_and_ownership():
    ciw = target()
    sch = target("sch", window="window:2")
    sym = target("sym", window="window:3", view="symbol")
    with ContextBroker(2) as broker:
        first = DemoPeer(broker, ciw)
        for context in (ciw, sch, sym):
            broker.register_target(context)
            broker.register_session("a", context)
        replacement = DemoPeer(broker, target(generation="next"))
        try:
            assert broker.bridge_snapshot()["sessions"] == {}
            assert broker.bindings.owners == {}
            with pytest.raises(CircuitCallError):
                call(broker, "a", sch)
            broker.register_session("b", replacement.context)
        finally:
            first.close()
            replacement.close()


def test_bridge_multiple_targets_rejects_foreign_rpc_and_preserves_default(tmp_path):
    ciw = target()
    sch, sym = target("sch", window="window:2"), target("sym", window="window:3", view="symbol")
    with InstanceBridge(tmp_path, ciw) as bridge:
        peer = DemoPeer(bridge.broker, ciw)
        try:
            with (
                BridgeClient.discover(tmp_path, ciw) as a,
                BridgeClient.discover(tmp_path, ciw) as b,
            ):
                a.register_session("a", ciw)
                b.register_session("b", ciw)
                for context in (sch, sym):
                    a.register_target(context)
                    a.register_session("a", context)
                assert a.bridge_snapshot()["sessions"]["a"]["target_id"] == "ciw"
                a.select_session_target("a", sym)
                assert call(a, "a", sch)["valid"]
                assert call(a, "a", sym)["valid"]
                with pytest.raises(CircuitCallError) as caught:
                    call(b, "b", sch)
                assert caught.value.code == "binding_target_not_owned"
                with pytest.raises(CircuitCallError) as conflict:
                    b.register_session("b", sch)
                assert conflict.value.data["owner_session_id"] == "a"
                assert call(b, "b", ciw)["valid"]
                a.release_target(sch)
                assert bridge.targets.get("sch") == sch
                assert call(a, "a", sch)["valid"]
                assert a.bridge_snapshot()["sessions"]["a"]["target_id"] == "sym"
                a.release_session("a")
                b.register_target(sch)
                b.register_session("b", sch)
        finally:
            peer.close()


@pytest.mark.parametrize("close_owner", [False, True])
def test_queued_target_is_immutable_and_close_prevents_dispatch(close_owner):
    ciw = target()
    sch, sym = target("sch", window="window:2"), target("sym", window="window:3", view="symbol")
    with ContextBroker(2) as broker:
        peer = connect_peer(broker, ciw)
        try:
            broker.register_session("blocker", ciw)
            for context in (sch, sym):
                broker.register_target(context)
                broker.register_session("a", context)
            queued = threading.Event()
            with ThreadPoolExecutor(2) as pool:
                first = pool.submit(call, broker, "blocker", ciw)
                request = peer.receive()
                pending = pool.submit(
                    call,
                    broker,
                    "a",
                    sch,
                    progress=lambda row: queued.set() if row["state"] == "queued" else None,
                )
                assert queued.wait(3)
                broker.select_session_target("a", sym)
                if close_owner:
                    broker.release_session("a")
                    with pytest.raises(CircuitCallError, match="owned"):
                        broker.register_session("b", sch)
                reply(peer, request, "blocker")
                assert first.result(3)["value"] == "blocker"
                if close_owner:
                    with pytest.raises(CircuitCallError) as caught:
                        pending.result(3)
                    assert caught.value.code == "binding_target_not_owned"
                    broker.register_session("b", sch)
                else:
                    next_request = peer.receive()
                    assert next_request["target_id"] == "sch"
                    reply(peer, next_request, "schematic")
                    assert pending.result(3)["value"] == "schematic"
                    assert broker.bridge_snapshot()["sessions"]["a"]["target_id"] == "sym"
        finally:
            peer.close()


def test_running_target_cannot_be_taken_after_owner_closes():
    sch = target("sch", window="window:2")
    with ContextBroker(2) as broker:
        peer = connect_peer(broker, sch)
        try:
            broker.register_session("a", sch)
            with ThreadPoolExecutor(1) as pool:
                active = pool.submit(call, broker, "a", sch)
                request = peer.receive()
                broker.release_session("a")
                assert broker.bridge_snapshot()["sessions"]["a"]["state"] == "release_pending"
                with pytest.raises(CircuitCallError):
                    broker.register_session("b", sch)
                reply(peer, request, "complete")
                assert active.result(3)["value"] == "complete"
                broker.register_session("b", sch)
        finally:
            peer.close()


def test_unknown_read_holds_owner_until_matching_late_reply():
    sch = target("sch", window="window:2")
    with ContextBroker(2) as broker:
        peer = connect_peer(broker, sch)
        try:
            broker.register_session("a", sch)
            with ThreadPoolExecutor(1) as pool:
                active = pool.submit(call, broker, "a", sch)
                request = peer.receive()
                peer.send(
                    {
                        "protocol": VERSION,
                        "kind": "response",
                        "generation": "g",
                        "id": request["id"],
                        "ok": False,
                        "code": "skill_timeout",
                    }
                )
                with pytest.raises(NeedsReconcile):
                    active.result(3)
            broker.release_session("a")
            with pytest.raises(CircuitCallError):
                broker.register_session("b", sch)
            router = broker.registry.get("instance", "g")
            router.clear_unknown("unrelated")
            with pytest.raises(CircuitCallError):
                broker.register_session("b", sch)
            router.clear_unknown(request["id"])
            broker.register_session("b", sch)
        finally:
            peer.close()


def test_unknown_write_retains_ownership_after_transport_reply_is_known():
    sch = target("sch", window="window:2")
    with ContextBroker(2) as broker:
        peer = connect_peer(broker, sch)
        try:
            broker.register_session("a", sch)

            def stop():
                with skill_call_context(session_id="a"):
                    return broker.circuit_call(
                        sch,
                        {
                            "function": "aiSimStop",
                            "values": ["run"],
                            "claim": None,
                            "expected": None,
                        },
                    )

            with ThreadPoolExecutor(1) as pool:
                active = pool.submit(stop)
                request = peer.receive()
                peer.send(
                    {
                        "protocol": VERSION,
                        "kind": "response",
                        "generation": "g",
                        "id": request["id"],
                        "ok": False,
                        "code": "circuit_outcome_unknown",
                        "diagnostic": {"category": "outcome_unknown", "operation_dispatched": True},
                    }
                )
                with pytest.raises(NeedsReconcile):
                    active.result(3)
            assert broker.router_snapshot(sch)["unknown"] is None
            broker.release_session("a")
            state = broker.bridge_snapshot()["sessions"]["a"]
            assert state["holds"] == {request["id"]: "unknown"}
            assert state["operations"][0]["source"] == sch.record()
            with pytest.raises(CircuitCallError):
                broker.register_session("b", sch)
        finally:
            peer.close()


@pytest.mark.parametrize("finish", [False, True])
def test_running_simulation_holds_ownership_until_exact_run_finishes(finish):
    ade = target("ade", window="swindow:5", view="maestro")
    with ContextBroker(2) as broker:
        peer = connect_peer(broker, ade)
        try:
            broker.register_session("a", ade)

            def status():
                with skill_call_context(session_id="a"):
                    return broker.circuit_call(
                        ade,
                        {
                            "function": "aiSimStatus",
                            "values": ["simulation-run:test"],
                            "claim": None,
                            "expected": None,
                        },
                    )

            with ThreadPoolExecutor(1) as pool:
                for state, run_ref in [
                    ("running", "simulation-run:test"),
                    ("completed", "simulation-run:other"),
                ] + ([("completed", "simulation-run:test")] if finish else []):
                    pending = pool.submit(status)
                    request = peer.receive()
                    peer.send(
                        {
                            "protocol": VERSION,
                            "kind": "response",
                            "generation": "g",
                            "id": request["id"],
                            "ok": True,
                            "result": {
                                "valid": True,
                                "circuit": {"ok": True, "state": state, "run_ref": run_ref},
                            },
                        }
                    )
                    assert pending.result(3)["circuit"]["state"] == state
            broker.release_session("a")
            if finish:
                broker.register_session("b", ade)
            else:
                assert broker.bridge_snapshot()["sessions"]["a"]["holds"] == {
                    "simulation-run:test": "running"
                }
                with pytest.raises(CircuitCallError):
                    broker.register_session("b", ade)
        finally:
            peer.close()


def test_controller_keeps_previous_targets_after_quick_input(tmp_path):
    from sico.adapters.virtuoso import context_tools
    from sico.service.python_backend import PythonBackend
    from sico.providers.mock import ContextDemoProvider
    from sico.service.controller import SessionController
    from sico.storage.journal import SessionJournal

    ciw = target()
    with ContextBroker(2) as broker, SessionJournal(tmp_path, "multi") as journal:
        peer = DemoPeer(broker, ciw)
        controller = SessionController(
            PythonBackend(ContextDemoProvider(), context_tools(broker), journal, ciw)
        )
        controller.bindings.attach_targets(broker)
        try:
            for context in (
                target("sch", window="window:2"),
                target("sym", window="window:3", view="symbol"),
            ):
                assert controller.accept(
                    {
                        "kind": "submit",
                        "id": context.target_id,
                        "text": "Read context",
                        "context": context.record(),
                    }
                )
                assert controller.wait(3)
                assert not controller.fault
            snapshot = controller.updates.read_updates()["snapshot"]
            assert {row["target_id"] for row in snapshot["bindings"]} == {"ciw", "sch", "sym"}
            assert controller.bindings.released_targets() == []
            assert broker.context("instance", "sch").target_id == "sch"
            assert broker.bridge_snapshot()["sessions"]["multi"]["default_target_id"] == "sym"
        finally:
            controller.close()
            controller.wait(3)
            peer.close()


def test_picker_passes_its_logical_session_identity():
    from sico.service.targets import BridgeTargetProvider
    from sico.transport.broker import _CALL_CONTEXT

    seen = []

    class Broker:
        def circuit_call(self, context, params):
            seen.append(dict(_CALL_CONTEXT.get()))
            return {"circuit": {"ok": True, "libraries": []}}

    provider = BridgeTargetProvider(Broker(), target(), "picker-owner")
    assert provider.libraries() == []
    assert seen == [{"session_id": "picker-owner"}]
    assert _CALL_CONTEXT.get() == {}


def test_binding_registry_treats_symlinked_project_spellings_as_one_project(tmp_path):
    """The launcher may keep its shell alias while a snapshot carries the real path."""

    real = tmp_path / "project"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real)
    registry = BindingRegistry()
    anchor = target("session", window="window:1")
    anchor.snapshot["cwd"] = str(alias)
    assert registry.add("session", anchor).anchor.snapshot["cwd"] == str(alias)

    quick = target("quick", window="window:1")
    quick.snapshot["cwd"] = str(real)
    record = registry.add("session", quick)  # Same project, other spelling.
    assert set(record.targets) >= {"session", "quick"}

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    foreign = target("other", window="window:2", cell="OtherAmp")
    foreign.snapshot["cwd"] = str(elsewhere)
    with pytest.raises(ValueError, match="another captured project"):
        registry.add("session", foreign)
