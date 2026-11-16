# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent transport contract cancellation contracts."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from agent_transport_contract_support import context

from sico.core.contracts import BoundContext, NeedsReconcile
from sico.demo import DemoPeer
from sico.transport.broker import ContextBroker, skill_call_context
from sico.transport.methods import QueryUnavailable
from sico.transport.router import InstanceRegistry, SkillInstanceRouter


def test_router_cancel_request_removes_queued_work_and_preserves_fifo_owner():
    router = SkillInstanceRouter("i", "g", queue_limit=2)
    entered, release = threading.Event(), threading.Event()
    queued, following = threading.Event(), threading.Event()
    queued_states, following_states, dispatched = [], [], []

    def operation(request_id):
        dispatched.append(request_id)
        if len(dispatched) == 1:
            entered.set()
            assert release.wait(5)
        return {"ok": True}

    def progress(states, ready):
        def receive(record):
            states.append(record)
            if record["state"] == "queued":
                ready.set()
        return receive

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(router.execute, "read", operation)
        try:
            assert entered.wait(2)
            second = pool.submit(router.execute, "read", operation, wait=True,
                                 progress=progress(queued_states, queued), session_id="owner")
            assert queued.wait(2)
            third = pool.submit(router.execute, "read", operation, wait=True,
                                progress=progress(following_states, following), session_id="other")
            assert following.wait(2)
            request_id = queued_states[-1]["request_id"]
            snapshot = router.snapshot()
            assert snapshot["active_request"]["state"] == "running"
            assert [row["queue_position"] for row in snapshot["queue"]] == [1, 2]
            assert all(row["blocked_by"] == snapshot["active"] for row in snapshot["queue"])
            snapshot["queue"][0]["session_id"] = "mutated"
            assert router.snapshot()["queue"][0]["session_id"] == "owner"
            assert router.cancel_request(request_id, session_id="wrong") == "not_owner"
            assert router.cancel_request(request_id, session_id="owner") == "cancel_requested"
            with pytest.raises(QueryUnavailable) as raised:
                second.result(2)
            assert raised.value.code == "cancelled_before_start"
            assert [row["state"] for row in queued_states] == [
                "accepted", "queued", "cancelled_before_start",
            ]
            assert queued_states[-1]["cancel_requested_at"] <= queued_states[-1]["finished_at"]
            assert router.snapshot()["queued"] == 1
            assert router.snapshot()["queue"][0]["queue_position"] == 1
            assert not first.done() and not third.done()
            assert router.cancel_request(request_id) == "not_found"
        finally:
            release.set()
        assert first.result(2) == third.result(2) == {"ok": True}
    assert dispatched == [dispatched[0], following_states[0]["request_id"]]
    assert request_id not in dispatched
    assert router._requests == {}


def test_router_cancel_request_does_not_claim_running_request_is_cancelled():
    router = SkillInstanceRouter("i", "g")
    entered, release = threading.Event(), threading.Event()
    states = []

    def operation(_request_id):
        entered.set()
        assert release.wait(5)
        return {"ok": True}

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(router.execute, "read", operation, session_id="owner",
                             progress=states.append)
        try:
            assert entered.wait(2)
            active = router.snapshot()["active"]
            assert active
            assert router.cancel_request(active, session_id="wrong") == "not_owner"
            assert router.cancel_request(active, session_id="owner") == "running"
            assert router.snapshot()["active"] == active
        finally:
            release.set()
        assert worker.result(2) == {"ok": True}
    assert [row["state"] for row in states] == ["accepted", "running", "completed"]
    assert router.cancel_request(active, session_id="owner") == "not_found"


def test_router_cancel_preserves_timed_out_request_until_matching_late_reply():
    router = SkillInstanceRouter("i", "g")
    states = []

    def timeout(request_id):
        router.mark_unknown(request_id)
        assert router.cancel_request(request_id, session_id="owner") == "running_unknown"
        raise QueryUnavailable("skill_timeout", "Execution result is unknown")

    with pytest.raises(QueryUnavailable):
        router.execute("read", timeout, session_id="owner", target_id="target",
                       progress=states.append)
    request_id = states[-1]["request_id"]
    assert router.snapshot()["active"] is None
    unknown = router.snapshot()["unknown_request"]
    assert unknown["request_id"] == request_id
    assert unknown["session_id"] == "owner" and unknown["target_id"] == "target"
    assert unknown["state"] == "timed_out_unknown"
    assert unknown["accepted_at"] <= unknown["started_at"] <= unknown["finished_at"]
    assert router.cancel_request(request_id, session_id="wrong") == "not_owner"
    assert router.cancel_request(request_id, target_id="wrong") == "not_owner"
    assert router.cancel_request(request_id, session_id="owner") == "running_unknown"
    with pytest.raises(QueryUnavailable) as blocked:
        router.execute("read", lambda _request_id: pytest.fail("Unknown slot was released"))
    assert blocked.value.code == "skill_blocked_unknown"
    router.clear_unknown("unrelated")
    assert router.cancel_request(request_id, session_id="owner") == "running_unknown"
    router.clear_unknown(request_id)
    assert router.snapshot()["unknown_request"] is None
    assert router.cancel_request(request_id, session_id="owner") == "not_found"
    assert router._requests == {}
    assert router.execute("read", lambda _request_id: {"ok": True}) == {"ok": True}


def test_router_cancel_at_admission_is_idempotent_and_prevents_dispatch():
    router = SkillInstanceRouter("i", "g")
    states = []

    def progress(record):
        states.append(record)
        if record["state"] == "accepted":
            assert router.cancel_request(record["request_id"]) == "cancel_requested"
            assert router.cancel_request(record["request_id"]) == "cancel_requested"

    with pytest.raises(QueryUnavailable) as raised:
        router.execute("read", lambda _request_id: pytest.fail("Cancelled work was dispatched"),
                       progress=progress)
    assert raised.value.code == "cancelled_before_start"
    assert [row["state"] for row in states] == ["accepted", "cancelled_before_start"]
    assert router.snapshot()["queued"] == 0
    assert router._requests == {}


def test_registry_cancel_rejects_closed_or_replaced_generation():
    registry = InstanceRegistry()
    router = registry.register("i", "old")
    router.close()
    assert registry.cancel("i", "old", "a" * 32) == "router_unavailable"
    registry.register("i", "new")
    assert registry.cancel("i", "old", "a" * 32) == "router_unavailable"
    assert registry.cancel("i", "new", "a" * 32) == "not_found"
    registry.close()


def test_broker_cancel_validates_owner_target_generation_and_request_id():
    with ContextBroker(2) as broker:
        peer = DemoPeer(broker, context("cancel"))
        router = broker.registry.get("cancel", "g")
        other = BoundContext("cancel", "g", "other", peer.context.snapshot)
        broker.register_target(other)
        broker.register_session("owner", peer.context)
        states, checked = [], []

        def progress(record):
            states.append(record)
            if record["state"] != "accepted":
                return
            request_id = record["request_id"]
            checked.append(broker.cancel_request(peer.context, request_id, session_id="wrong"))
            checked.append(broker.cancel_request(other, request_id, session_id="owner"))
            checked.append(broker.cancel_request(context("cancel", "old"), request_id,
                                                 session_id="owner"))
            checked.append(broker.cancel_request(peer.context, request_id, session_id="owner"))

        try:
            for invalid_id in ("", "x" * 32, "A" * 32, None, "a" * 33):
                with pytest.raises(ValueError, match="Invalid router request ID"):
                    broker.cancel_request(peer.context, invalid_id, session_id="owner")
            with pytest.raises(ValueError, match="invalid identifier"):
                broker.cancel_request(peer.context, "a" * 32, session_id="")
            with pytest.raises(NeedsReconcile):
                broker.cancel_request(context("cancel", valid=False), "a" * 32, session_id="owner")
            with skill_call_context(session_id="owner", progress=progress):
                with pytest.raises(QueryUnavailable) as raised:
                    broker.get_context(peer.context)
            assert raised.value.code == "cancelled_before_start"
            assert checked == ["not_owner", "not_owner", "router_unavailable", "cancel_requested"]
            assert [row["state"] for row in states] == ["accepted", "cancelled_before_start"]
            assert router.snapshot()["active"] is None
            assert broker.get_context(peer.context)["valid"]
        finally:
            peer.close()


def test_broker_router_snapshot_rejects_stale_target_and_replacement_during_read(monkeypatch):
    with ContextBroker(2) as broker:
        peer = DemoPeer(broker, context("status"))
        replacement = None
        entered, release = threading.Event(), threading.Event()
        router = broker.registry.get("status", "g")
        snapshot = router.snapshot

        def slow_snapshot():
            entered.set()
            assert release.wait(5)
            return snapshot()

        try:
            assert broker.router_snapshot(peer.context)["router_id"] == router.router_id
            assert broker.router_snapshot(context("status", "stale")) is None
            assert broker.router_snapshot(context("status", valid=False)) is None
            monkeypatch.setattr(router, "snapshot", slow_snapshot)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(broker.router_snapshot, peer.context)
                try:
                    assert entered.wait(2)
                    replacement = DemoPeer(broker, context("status", "next"))
                finally:
                    release.set()
                assert pending.result(2) is None
            assert broker.router_snapshot(peer.context) is None
            assert broker.router_snapshot(replacement.context)["generation"] == "next"
        finally:
            release.set()
            peer.close()
            if replacement:
                replacement.close()
