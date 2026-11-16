# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent native binding contract admission contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from agent_native_binding_contract_support import dispatch, output, parameters
from test_agent_bridge import connect_peer, reply
from test_agent_session_bindings import target

from sico.core.contracts import BoundContext, CircuitCallError
from sico.demo import DemoPeer
from sico.transport.broker import ContextBroker, skill_call_context
from sico.transport.framing import VERSION


def test_resource_alias_conflicts_before_write_and_atomic_multi_reserve(tmp_path):
    ciw = target()
    with ContextBroker(2) as broker:
        peer = DemoPeer(broker, ciw)
        try:
            for session in ("owner", "other"):
                broker.register_session(session, ciw)
            dispatch(broker)
            with pytest.raises(CircuitCallError) as error:
                broker.native_bindings.dispatch(
                    "other",
                    ciw,
                    "circuit_call",
                    dict(
                        function="aiCopilotOpenTarget",
                        values=["Alias", "Amp", "schematic", "schematic"],
                        claim=None,
                        expected=None,
                    ),
                    "b" * 32,
                    resources=[["Alias", "Amp", "schematic", "/worklib"]],
                )
            assert (
                error.value.code == "binding_conflict"
                and not error.value.data["operation_dispatched"]
            )
            assert not broker.binding_snapshot("other")["reservations"]
            free = target("free", window="window:7", cell="Free")
            with pytest.raises(CircuitCallError):
                broker.binding_events.reserve_many("other", [free, output()], "multi")
            assert not broker.binding_snapshot("other")["reservations"]
            broker.bindings.add("owner", free)
            row = broker.binding_snapshot("owner")["reservations"][0]
            broker._sessions["owner"].holds["write"] = "outcome_unknown"
            with pytest.raises(CircuitCallError):
                broker.release_binding_resource("owner", row["reservation_id"])
            broker.release_session("owner")
            assert "owner" in broker._sessions and broker._sessions["owner"].reservations
        finally:
            peer.close()


def test_preflight_read_has_distinct_request_and_reservation_precedes_mutation():
    ciw = target()
    ciw = BoundContext(
        ciw.instance_id,
        ciw.generation,
        ciw.target_id,
        {**ciw.snapshot, "native_binding": "cad_ai_native_binding.v1"},
    )
    with ContextBroker(2) as broker:
        peer = connect_peer(broker, ciw)
        try:
            broker.register_session("owner", ciw)

            def call():
                with skill_call_context(session_id="owner"):
                    return broker.circuit_call(ciw, parameters())

            with ThreadPoolExecutor(1) as pool:
                pending = pool.submit(call)
                preflight = peer.receive()
                assert preflight["params"]["function"] == "aiCopilotResolveResources"
                assert not broker.binding_snapshot("owner")["reservations"]
                peer.send(
                    dict(
                        protocol=VERSION,
                        kind="response",
                        generation="g",
                        id=preflight["id"],
                        ok=True,
                        result=dict(
                            valid=True,
                            circuit=dict(
                                ok=True, resources=[["Work", "Amp", "schematic", "/worklib"]]
                            ),
                        ),
                    )
                )
                write = peer.receive()
                assert write["id"] != preflight["id"]
                assert write["native_identity"]["resources"] == [
                    ["Work", "Amp", "schematic", "/worklib"]
                ]
                assert broker.binding_snapshot("owner")["reservations"]
                reply(peer, write, "done")
                assert pending.result(3)["value"] == "done"
        finally:
            peer.close()


def test_guarded_read_reserves_its_declared_target_before_dispatch():
    """`run_simulation_setup` reads the retained model list before `aiSimRun`.

    That read carries the operation's expected target, so the broker authenticates
    and reserves the same row it reserves for the write instead of dispatching an
    empty reservation table that the native gate must reject.
    """
    ciw = target()
    ciw = BoundContext(
        ciw.instance_id,
        ciw.generation,
        ciw.target_id,
        {**ciw.snapshot, "native_binding": "cad_ai_native_binding.v1"},
    )
    expected = [
        "task:test",
        "foreground",
        ["Work", "Amp", "maestro"],
        "maestro",
        "digest",
        "/worklib",
        "/run",
    ]
    with ContextBroker(2) as broker:
        peer = connect_peer(broker, ciw)
        try:
            broker.register_session("owner", ciw)

            def call():
                with skill_call_context(session_id="owner"):
                    return broker.circuit_call(
                        ciw,
                        dict(
                            function="aiSimModels",
                            values=["simulation:setup"],
                            claim=["session", "simulation-run:setup", "digest"],
                            expected=expected,
                        ),
                    )

            with ThreadPoolExecutor(1) as pool:
                pending = pool.submit(call)
                # Answer whichever request arrives so a source regression fails the
                # assertions below instead of blocking the worker on a reply.
                observed = [peer.receive()]
                if observed[0]["params"]["function"] == "aiCopilotResolveResources":
                    assert not broker.binding_snapshot("owner")["reservations"]
                    peer.send(
                        dict(
                            protocol=VERSION,
                            kind="response",
                            generation="g",
                            id=observed[0]["id"],
                            ok=True,
                            result=dict(
                                valid=True,
                                circuit=dict(
                                    ok=True, resources=[["Work", "Amp", "maestro", "/worklib"]]
                                ),
                            ),
                        )
                    )
                    observed.append(peer.receive())
                reply(peer, observed[-1], "models")
                assert pending.result(3)["value"] == "models"
                assert observed[0]["params"]["function"] == "aiCopilotResolveResources"
                assert observed[0]["params"]["values"] == [
                    "aiSimModels",
                    ["simulation:setup"],
                    expected,
                ]
                assert observed[1]["params"]["function"] == "aiSimModels"
                assert observed[1]["native_identity"]["resources"] == [
                    ["Work", "Amp", "maestro", "/worklib"]
                ]
                reserved = broker.binding_snapshot("owner")["reservations"]
                assert [row["context"]["snapshot"]["cellview"] for row in reserved] == [
                    {"lib": "Work", "cell": "Amp", "view_name": "maestro"}
                ]
        finally:
            peer.close()


def test_reservation_journal_failure_prevents_ownership_and_dispatch(tmp_path):
    ciw = target()
    with ContextBroker(2) as broker:
        peer = DemoPeer(broker, ciw)
        try:
            broker.register_session("owner", ciw)
            before = deepcopy(broker.bindings.owners)

            def fail(_):
                raise OSError("disk full")

            broker.binding_events.persist = fail
            with pytest.raises(OSError, match="disk full"):
                dispatch(broker)
            assert broker.bindings.owners == before and not broker.native_bindings.requests
        finally:
            peer.close()
