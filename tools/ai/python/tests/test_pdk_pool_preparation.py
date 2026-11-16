"""Out-of-order checkpoint and final-publication behavior for parallel collection."""
from copy import deepcopy
import threading

import pytest

from cadai.pdk_preparation import PdkPreparation
from cadai.pdk_schema import PdkUnavailable
from pdk_fixture import Bridge
from test_pdk_preparation import context_with_pdk, finish


class Pool:
    configuration = {"test": "stable"}
    worker_limit, active, started = 2, 2, 2
    instances = []
    failure = None

    def __init__(self, root, env, origin, library, rows, probe):
        self.cancelled = threading.Event()
        self.rows, self.origin, self.probe = list(reversed(rows)), origin, probe
        self.closed = False
        self.instances.append(self)

    def start(self):
        pass

    def check(self):
        if self.cancelled.is_set():
            raise PdkUnavailable("collection_cancelled", "cancelled")

    def drain(self):
        if self.failure:
            raise self.failure
        row = self.rows.pop(0)
        cell = row["identity"]["target"]["cell"]
        capture = deepcopy(self.probe)
        capture["data"]["identity"] = deepcopy(row["identity"])
        capture["context"].update(source="background_dbAccess_worker", process_id=456)
        return [("running", cell, None), ("done", cell, capture)]

    def close(self):
        self.cancelled.set()
        self.closed = True


def create(tmp_path, cls=Pool):
    bridge = Bridge()
    context_with_pdk(bridge)
    return bridge, PdkPreparation(bridge, workspace=tmp_path, environment={}, pool_factory=cls)


def advance(service, job):
    return service.advance({k: job[k] for k in ("collection_ref", "next_batch")})


def test_resume_out_of_order_only_schedules_unfinished_tasks(tmp_path):
    bridge, first = create(tmp_path)
    progress = advance(first, first.prepare({}))
    assert progress["detailed_devices"] == 1
    job = first.jobs[progress["collection_ref"]]
    assert job["tasks"]["cell2"]["state"] == "done"
    first.close()
    resumed = PdkPreparation(bridge, workspace=tmp_path, environment={}, pool_factory=Pool)
    restart = resumed.prepare({})
    assert restart["collection_ref"] == progress["collection_ref"]
    assert restart["detailed_devices"] == 1
    next_result = advance(resumed, restart)
    assert all(row["identity"]["target"]["cell"] != "cell2" for row in Pool.instances[-1].rows)
    done = finish(resumed, next_result)
    assert done["status"] == "ready" and done["device_count"] == 3
    assert all(t["attempts"] == 1 for t in resumed.jobs[done["collection_ref"]]["tasks"].values())
    assert Pool.instances[-1].closed


def test_inspection_error_stops_pool_and_never_publishes(tmp_path):
    bridge, service = create(tmp_path)
    progress = advance(service, service.prepare({}))
    def unavailable(*args):
        raise PdkUnavailable("bridge_unavailable", "disconnected")
    bridge.capture = unavailable
    failed = advance(service, progress)
    assert failed["status"] == "failed" and failed["code"] == "bridge_unavailable"
    assert Pool.instances[-1].closed
    assert not list(service.data.root.glob("*.pdk.json"))


def test_duplicate_batch_does_not_drain_twice_and_cancel_is_final(tmp_path):
    bridge, service = create(tmp_path)
    initial = service.prepare({})
    progress = advance(service, initial)
    assert advance(service, initial)["detailed_devices"] == 1
    cancelled = service.cancel({"collection_ref": progress["collection_ref"]})
    assert cancelled["status"] == "cancelled" and Pool.instances[-1].closed
    assert service.prepare({})["status"] == "cancelled"
    assert not list(service.data.root.glob("*.pdk.json"))


def test_cancel_during_final_live_check_prevents_publication(tmp_path):
    bridge, service = create(tmp_path)
    job = service.prepare({})
    job = advance(service, advance(service, job))
    capture = bridge.capture
    def cancel_at_publish(op, target):
        if op == "libraries" and not service._pools:
            service.cancel({"collection_ref": job["collection_ref"]})
        return capture(op, target)
    bridge.capture = cancel_at_publish
    assert advance(service, job)["status"] == "cancelled"
    assert not list(service.data.root.glob("*.pdk.json"))


def test_changed_origin_during_prepare_reaps_old_pool(tmp_path):
    bridge, service = create(tmp_path)
    job = advance(service, service.prepare({}))
    bridge.ctx["session_ref"] = "changed"
    new = service.prepare({})
    assert new["collection_ref"] != job["collection_ref"]
    assert Pool.instances[-1].closed
    assert service.jobs[job["collection_ref"]]["status"] == "failed"


def test_corrupt_done_set_cannot_skip_tasks(tmp_path):
    bridge, service = create(tmp_path)
    result = advance(service, service.prepare({}))
    job = service.jobs[result["collection_ref"]]
    job["tasks"]["cell0"]["state"] = "done"
    assert not service.data.checkpoint_valid(job)
    service.close()


def test_refresh_rejection_keeps_active_collection_running(tmp_path):
    bridge, service = create(tmp_path)
    progress = advance(service, service.prepare({}))
    with pytest.raises(PdkUnavailable) as error:
        service.prepare({"refresh": True})
    assert error.value.code == "collection_active"
    assert not Pool.instances[-1].closed
    assert finish(service, progress)["status"] == "ready"
