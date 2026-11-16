"""Live router snapshots preserve ownership while remaining local to the worker."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from sico.core.contracts import BoundContext
from sico.demo import DemoPeer
from sico.service.metadata import MetadataService
from sico.service.router_status import router_status
from sico.transport.broker import ContextBroker
from sico.transport.methods import QueryUnavailable
from sico.transport.router import SkillInstanceRouter


def context(generation="g", target_id="bound", **extra):
    return BoundContext("status", generation, target_id, {"source": "simulated", **extra})


def test_metadata_samples_router_frequently_without_accelerating_name_polling():
    sampled = threading.Event()
    calls = {"status": [], "metadata": []}
    owner = threading.get_ident()

    def poll_status():
        calls["status"].append(threading.get_ident())
        if len(calls["status"]) >= 3:
            sampled.set()

    controller = SimpleNamespace(
        poll_router_status=poll_status,
        poll_metadata=lambda: calls["metadata"].append(threading.get_ident()),
    )
    metadata = MetadataService(lambda: (controller,), status_interval=0.01)
    try:
        assert sampled.wait(2)
    finally:
        metadata.close()
        assert metadata.wait(2)
    assert len(calls["status"]) >= 3 and len(calls["metadata"]) == 1
    assert all(thread != owner for thread in calls["status"] + calls["metadata"])


def test_queue_snapshot_tracks_fifo_position_and_preserves_detached_owner():
    router = SkillInstanceRouter("status", "g")
    entered, release = threading.Event(), threading.Event()
    queued, following = threading.Event(), threading.Event()

    def operation(request_id):
        entered.set()
        assert release.wait(5)
        return request_id

    def progress(event):
        def publish(record):
            if record["state"] == "queued":
                event.set()
        return publish

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(router.execute, "read", operation, session_id="owner")
        try:
            assert entered.wait(2)
            second = pool.submit(router.execute, "read", operation, wait=True,
                                 session_id="second", progress=progress(queued))
            assert queued.wait(2)
            third = pool.submit(router.execute, "read", operation, wait=True,
                                session_id="third", progress=progress(following))
            assert following.wait(2)
            snapshot = router.snapshot()
            assert snapshot["active_request"]["state"] == "running"
            assert snapshot["active_request"]["session_id"] == "owner"
            assert [row["queue_position"] for row in snapshot["queue"]] == [1, 2]
            assert all(row["blocked_by"] == snapshot["active"] for row in snapshot["queue"])
            snapshot["queue"][0]["session_id"] = "changed"
            assert router.snapshot()["queue"][0]["session_id"] == "second"
            assert router.cancel_request(snapshot["queue"][0]["request_id"]) == "cancel_requested"
            with pytest.raises(QueryUnavailable) as raised:
                second.result(2)
            assert raised.value.code == "cancelled_before_start"
            assert [row["queue_position"] for row in router.snapshot()["queue"]] == [1]
            assert not first.done() and not third.done()
        finally:
            release.set()
        first.result(2)
        third.result(2)
    assert router.snapshot()["queue"] == [] and router.snapshot()["active_request"] is None


def test_unknown_snapshot_keeps_request_identity_until_matching_reply():
    router = SkillInstanceRouter("status", "g")

    def timeout(_request_id):
        raise QueryUnavailable("skill_timeout", "Unknown outcome")

    with pytest.raises(QueryUnavailable):
        router.execute("read", timeout, session_id="owner", target_id="target", task_id="task")
    snapshot = router.snapshot()
    unknown = snapshot["unknown_request"]
    assert snapshot["active_request"] is None
    assert unknown["session_id"] == "owner" and unknown["target_id"] == "target"
    assert unknown["task_id"] == "task" and unknown["state"] == "timed_out_unknown"
    assert unknown["accepted_at"] <= unknown["started_at"] <= unknown["finished_at"]
    router.clear_unknown("unrelated")
    assert router.snapshot()["unknown_request"] == unknown
    router.clear_unknown(unknown["request_id"])
    assert router.snapshot()["unknown_request"] is None


def test_projection_filters_session_and_target_but_keeps_instance_owner():
    snapshot = SkillInstanceRouter("status", "g").snapshot()
    owner = {"request_id": "owner", "session_id": "other-session", "target_id": "other-target",
             "state": "running", "started_at": 1, "params": {"large": "private payload"}}
    own = {"request_id": "own", "session_id": "session", "target_id": "bound",
           "state": "queued", "queue_position": 2, "blocked_by": "owner"}
    snapshot.update(active="owner", active_request=owner, queued=3, queue=[
        dict(own, request_id="foreign", session_id="other-session", queue_position=1),
        own,
        dict(own, request_id="other-target", target_id="other-target", queue_position=3),
    ])
    status = router_status(snapshot, context(), "session")
    assert status["state"] == "running" and status["queued"] == 3
    assert status["requests"] == [own]
    assert status["owner"]["session_id"] == "other-session"
    assert "params" not in status["owner"]
    status["owner"]["session_id"] = "changed"
    status["requests"][0]["queue_position"] = 99
    assert owner["session_id"] == "other-session" and own["queue_position"] == 2


@pytest.mark.parametrize("change", [None, {"closed": True}, {"instance_id": "other"},
                                    {"generation": "old"}])
def test_projection_rejects_missing_closed_or_foreign_router(change):
    snapshot = SkillInstanceRouter("status", "g").snapshot() if change is not None else None
    if change is not None:
        snapshot.update(change)
    status = router_status(snapshot, context(), "session")
    assert status["state"] == "router_unavailable"
    assert status["owner"] is None and status["requests"] == []


@pytest.mark.parametrize("generation", ["g", "next"])
def test_broker_snapshot_rejects_stale_target_and_replacement_during_read(monkeypatch, generation):
    with ContextBroker(2) as broker:
        peer = DemoPeer(broker, context())
        replacement = None
        entered, release = threading.Event(), threading.Event()
        router = broker.registry.get("status", "g")
        snapshot = router.snapshot

        def slow_snapshot():
            entered.set()
            assert release.wait(5)
            return snapshot()

        def unexpected_rpc(*_args, **_kwargs):
            pytest.fail("Local router snapshots must not call Virtuoso")

        try:
            monkeypatch.setattr(broker.peers["status"], "call", unexpected_rpc)
            assert broker.router_snapshot(peer.context)["router_id"] == router.router_id
            assert broker.router_snapshot(context("stale")) is None
            assert broker.router_snapshot(context(valid=False)) is None
            assert broker.router_snapshot(context(target_id="unregistered")) is None
            monkeypatch.setattr(router, "snapshot", slow_snapshot)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(broker.router_snapshot, peer.context)
                try:
                    assert entered.wait(2)
                    replacement = DemoPeer(broker, context(generation))
                finally:
                    release.set()
                assert pending.result(2) is None
            assert broker.router_snapshot(replacement.context)["generation"] == generation
            if generation != "g":
                assert broker.router_snapshot(peer.context) is None
        finally:
            release.set()
            peer.close()
            if replacement:
                replacement.close()
