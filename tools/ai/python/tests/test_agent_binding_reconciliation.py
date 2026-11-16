"""M4 exact-request recovery through authenticated Bridge RPC and real journals."""

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_agent_bridge import connect_peer
from test_agent_session_bindings import target

from sico.adapters.bindings import register_bindings
from sico.core.contracts import BoundContext, CircuitCallError, NeedsReconcile, ToolCall
from sico.core.tools import ToolRegistry
from sico.transport.bridge_client import BridgeClient
from sico.transport.broker import skill_call_context
from sico.transport.framing import VERSION
from sico.transport.instance_bridge import InstanceBridge


def source():
    original = target("sch", window="window:2", library_path="/worklib")
    return BoundContext(
        original.instance_id,
        original.generation,
        original.target_id,
        dict(original.snapshot, capabilities=["circuit_call", "project_receipt"]),
    )


def parameters(key="first", function="aiCreateExecute"):
    run = function == "aiSimRun"
    return dict(
        function=function,
        values=["simulation:setup", [], key] if run else ["prepare", "d" * 64, key],
        claim=["native", ("simulation-run:" if run else "create-execute:") + key, "a" * 64],
        expected=[
            "task:design",
            "foreground",
            ["Work", "Amp", "schematic"],
            "schematic",
            "d" * 64,
            "/worklib",
            None,
        ],
    )


def business(key="first", **changes):
    return dict(
        ok=True,
        saved=True,
        circuit_ref="created:" + key,
        target=["Work", "Amp", "schematic"],
        **changes,
    )


@pytest.fixture
def host(tmp_path):
    context = source()
    with InstanceBridge(tmp_path, context) as bridge:
        peer = connect_peer(bridge.broker, context)
        with BridgeClient.discover(tmp_path, context) as client, ThreadPoolExecutor(4) as pool:
            client.register_session("owner", context)
            try:
                yield bridge, client, peer, pool
            finally:
                peer.close()


def start_unknown(host, key="first", *, timeout=False, function="aiCreateExecute"):
    bridge, client, peer, pool = host

    def call():
        with skill_call_context(session_id="owner", task_id="task"):
            return client.circuit_call(source(), parameters(key, function))

    future = pool.submit(call)
    request = peer.receive()
    peer.send(
        dict(
            protocol=VERSION,
            kind="response",
            generation="g",
            id=request["id"],
            ok=False,
            code="skill_timeout" if timeout else "circuit_outcome_unknown",
            diagnostic={"category": "outcome_unknown", "operation_dispatched": True},
        )
    )
    with pytest.raises(NeedsReconcile):
        future.result(5)
    assert request["id"] in client.binding_snapshot("owner")["holds"]
    return request["id"]


def reconcile(host, request_id):
    _, client, _, _ = host
    return client.reconcile_binding_operation(
        "owner", client.binding_snapshot("owner")["binding_id"], request_id
    )


def retained_reply(bridge, request_id, result=None, **changes):
    reply = dict(id=request_id, ok=True, result={"valid": True, "circuit": result or business()})
    reply.update(changes)
    assert bridge.router.record_reply(request_id, reply, late=True)
    return reply


def observation(host, future, native, *, checked=True):
    _, _, peer, _ = host
    read = peer.receive()
    assert read["method"] == "project_receipt"
    assert read["params"]["input_digest"] == "a" * 64
    peer.send(
        dict(
            protocol=VERSION,
            kind="response",
            generation="g",
            id=read["id"],
            ok=True,
            result={
                "valid": True,
                "project": {
                    "ok": True,
                    "receipt_json": json.dumps(native) if native is not None else None,
                    "run_context_checked": checked,
                },
            },
        )
    )
    return future.result(5)


def test_late_success_clears_exact_hold_and_keeps_ownership_without_replay(host):
    bridge, client, peer, _ = host
    request_id = start_unknown(host, timeout=True)
    unknown = reconcile(host, request_id)
    assert unknown["state"] == "unknown" and not unknown["hold_cleared"]
    peer.send(
        dict(
            protocol=VERSION,
            kind="skill_late_reply",
            generation="g",
            id=request_id,
            reply=dict(id=request_id, ok=True, result={"valid": True, "circuit": business()}),
        )
    )
    deadline = time.monotonic() + 3
    while bridge.router.snapshot()["unknown"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    result = reconcile(host, request_id)
    assert result["state"] == "completed" and result["hold_cleared"]
    assert not result["ownership_released"] and not result["automatic_resume_allowed"]
    before = len(bridge.broker.binding_events.events)
    assert reconcile(host, request_id) == result
    assert len(bridge.broker.binding_events.events) == before
    assert not client.binding_snapshot("owner")["holds"]
    assert client.binding_snapshot("owner")["reservations"]
    with pytest.raises(CircuitCallError):
        bridge.broker.register_session("foreign", source())


def test_two_unknown_writes_same_target_and_partial_result_are_independent(host):
    bridge, client, _, pool = host
    first = start_unknown(host)
    second = start_unknown(host, "second")
    partial = dict(business(), ok=False, saved=False, error="wire creation failed")
    result = observation(host, pool.submit(reconcile, host, first), partial)
    assert result["state"] == "partial_or_failed"
    assert result["evidence"]["business_receipt"] == partial
    assert set(client.binding_snapshot("owner")["holds"]) == {second}
    assert len(client.binding_snapshot("owner")["reservations"]) == 2
    retained_reply(bridge, second, business("second"))
    assert reconcile(host, second)["state"] == "completed"
    assert not client.binding_snapshot("owner")["holds"]


@pytest.mark.parametrize("change", ["target", "reference", "lifetime", "generation", "request"])
def test_conflicting_evidence_never_clears_hold(host, change):
    bridge, client, _, _ = host
    request_id = start_unknown(host)
    reply = retained_reply(bridge, request_id)
    if change == "target":
        reply["result"]["circuit"]["target"] = ["Work", "Other", "schematic"]
    elif change == "reference":
        reply["result"]["circuit"]["circuit_ref"] = "created:other"
    elif change == "request":
        reply["id"] = "b" * 32
    row = bridge.journal.lookup(request_id)
    row["reply"] = reply
    if change == "lifetime":
        row["binding_id"] = "old"
    if change == "generation":
        # Simulate a corrupted lookup; the durable writer correctly overwrites identity.
        original = bridge.journal.receipt
        bridge.journal.receipt = lambda *args, **kwargs: dict(
            original(*args, **kwargs), generation="old"
        )
    else:
        bridge.journal.record(row)
    result = reconcile(host, request_id)
    assert result["state"] == "evidence_conflict"
    assert request_id in client.binding_snapshot("owner")["holds"]


def test_missing_native_receipt_is_unknown_not_proof_of_non_dispatch(host):
    _, client, _, pool = host
    request_id = start_unknown(host)
    result = observation(host, pool.submit(reconcile, host, request_id), None)
    assert result["state"] == "unknown"
    assert request_id in client.binding_snapshot("owner")["holds"]


def test_explicit_native_rejection_is_known_not_dispatched(host):
    bridge, client, _, _ = host
    request_id = start_unknown(host)
    retained_reply(
        bridge,
        request_id,
        ok=False,
        diagnostic={
            "category": "binding",
            "operation_dispatched": False,
            "raw_error": "path changed",
        },
    )
    result = reconcile(host, request_id)
    assert result["state"] == "not_dispatched"
    assert (
        result["evidence"]["router_receipt"]["reply"]["diagnostic"]["raw_error"] == "path changed"
    )
    assert not client.binding_snapshot("owner")["holds"]


@pytest.mark.parametrize("terminal", ["completed", "failed", "stopped"])
def test_simulation_holds_until_exact_run_and_setup_terminal(host, terminal):
    _, client, _, pool = host
    request_id = start_unknown(host, function="aiSimRun")
    run = dict(
        ok=True,
        state="running",
        run_ref="simulation-run:first",
        setup_ref="simulation:setup",
        history="Interactive.0",
    )
    result = observation(host, pool.submit(reconcile, host, request_id), run)
    assert result["state"] == "running" and not result["hold_cleared"]
    assert request_id in client.binding_snapshot("owner")["holds"]
    wrong = dict(run, state=terminal, setup_ref="simulation:other")
    result = observation(host, pool.submit(reconcile, host, request_id), wrong)
    assert result["state"] == "evidence_conflict"
    result = observation(host, pool.submit(reconcile, host, request_id), dict(run, state=terminal))
    assert result["hold_cleared"]
    assert not client.binding_snapshot("owner")["holds"]


def test_foreign_client_and_retired_lifetime_cannot_reconcile(host, tmp_path):
    bridge, client, _, _ = host
    request_id = start_unknown(host)
    binding_id = client.binding_snapshot("owner")["binding_id"]
    with BridgeClient.discover(tmp_path, source()) as foreign:
        with pytest.raises(NeedsReconcile):
            foreign.reconcile_binding_operation("owner", binding_id, request_id)
    with pytest.raises(NeedsReconcile):
        client.reconcile_binding_operation("owner", "retired", request_id)
    retained_reply(bridge, request_id)
    assert reconcile(host, request_id)["hold_cleared"]


def test_persistence_failure_leaves_hold_and_terminal_receipt(host):
    bridge, client, _, _ = host
    request_id = start_unknown(host)
    retained_reply(bridge, request_id)

    def fail(event):
        raise OSError("disk full")

    bridge.broker.binding_events.persist = fail
    with pytest.raises(Exception):
        reconcile(host, request_id)
    assert request_id in bridge.broker._sessions["owner"].holds
    assert bridge.journal.lookup(request_id)["reply"]["ok"]
    assert bridge.broker.binding_events.fault == "disk full"


def test_duplicate_concurrent_reconciliation_and_native_event_hold(host):
    bridge, client, _, pool = host
    request_id = start_unknown(host)
    bridge.broker._sessions["owner"].holds["native_event_test"] = "event rejected"
    retained_reply(bridge, request_id)
    futures = [pool.submit(reconcile, host, request_id) for _ in range(3)]
    results = [future.result(5) for future in futures]
    assert all(row == results[0] for row in results)
    assert client.binding_snapshot("owner")["holds"] == {"native_event_test": "event rejected"}


def test_closed_source_can_use_surviving_anchor_for_exact_receipt(host):
    bridge, client, _, pool = host
    request_id = start_unknown(host)
    ciw = BoundContext(
        "instance", "g", "ciw", dict(source().snapshot, cellview=None, window="window:1")
    )
    client.register_target(ciw)
    client.register_session("owner", ciw)
    bridge.broker.binding_events.invalidate_target(source(), "window_closed")
    result = observation(host, pool.submit(reconcile, host, request_id), business())
    assert result["state"] == "completed"
    assert source().target_id in client.binding_snapshot("owner")["invalidated"]


def test_agent_tools_use_authenticated_session_and_reject_forged_evidence(host):
    bridge, client, _, _ = host
    request_id = start_unknown(host)
    registry = ToolRegistry()
    register_bindings(registry, client, "owner")
    state = registry.execute(ToolCall("list", "get_binding_operations", {}), source())
    assert request_id in state.data["holds"]
    invalid = registry.execute(
        ToolCall(
            "bad",
            "reconcile_binding_operation",
            {"request_id": request_id, "receipt": {"ok": True}},
        ),
        source(),
    )
    assert invalid.status == "invalid_arguments"
    retained_reply(bridge, request_id)
    result = registry.execute(
        ToolCall("reconcile", "reconcile_binding_operation", {"request_id": request_id}), source()
    )
    assert result.status == "ok" and result.data["hold_cleared"]
    schemas = {row["name"]: row for row in registry.schemas()}
    assert schemas["reconcile_binding_operation"]["annotations"]["readOnlyHint"] is False
