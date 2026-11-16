"""One authority arbitrates pending work, attaches, explicit stop and idle exit."""

import pytest

from sico.service.service_allocation import capture_allocation
from sico.service.service_lifecycle import PendingWork, ServiceLifecycle


@pytest.mark.parametrize("category", ["sessions", "queued", "approvals", "jobs", "reconcile"])
def test_pending_work_blocks_stop_and_idle_then_starts_a_fresh_idle_period(category):
    now = [0.0]
    pending = [PendingWork(**{category: 1})]
    lifecycle = ServiceLifecycle(idle_seconds=10, pending=lambda: pending[0], clock=lambda: now[0])
    now[0] = 100
    lifecycle.tick()
    assert not lifecycle.stopping
    assert lifecycle.stop()["pending"][category] == 1
    assert not lifecycle.stopping and lifecycle.status()["idle_remaining"] is None
    pending[0] = PendingWork()
    lifecycle.tick()
    assert lifecycle.status()["idle_remaining"] == 10
    now[0] += 9
    lifecycle.tick()
    assert not lifecycle.stopping
    now[0] += 1
    lifecycle.tick()
    assert lifecycle.stopping and lifecycle.status()["reason"] == "idle"


@pytest.mark.parametrize("first", ["attach", "idle", "stop"])
def test_attach_and_exit_cannot_both_win_the_same_turn(first):
    now = [0.0]
    lifecycle = ServiceLifecycle(idle_seconds=10, clock=lambda: now[0])
    now[0] = 10
    if first == "attach":
        assert lifecycle.attach()
        lifecycle.tick()
        assert not lifecycle.stopping
        assert lifecycle.status()["idle_remaining"] == 10
    elif first == "idle":
        lifecycle.tick()
        assert not lifecycle.attach()
        assert lifecycle.status()["reason"] == "idle"
    else:
        lifecycle.stop()
        assert not lifecycle.attach()
        assert lifecycle.status()["reason"] == "requested"


def test_status_polling_does_not_extend_idle_deadline():
    now = [0.0]
    lifecycle = ServiceLifecycle(idle_seconds=10, clock=lambda: now[0])
    for second in range(11):
        now[0] = second
        assert lifecycle.status()["idle_remaining"] == 10 - second
    lifecycle.tick()
    assert lifecycle.stopping


def test_ready_reply_in_flight_defers_idle_only_until_its_connection_is_released():
    now = [0.0]
    lifecycle = ServiceLifecycle(idle_seconds=10, clock=lambda: now[0])
    assert lifecycle.attach()
    now[0] = 20
    lifecycle.tick(attaching=True)
    assert not lifecycle.stopping
    now[0] = 30
    lifecycle.tick(attaching=False)
    assert lifecycle.stopping


@pytest.mark.parametrize("value", [-1, True, 0.5, "1", 2**31])
def test_pending_counts_are_bounded_nonnegative_integers(value):
    with pytest.raises(ValueError):
        PendingWork(jobs=value)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_idle_interval_must_be_finite_and_positive(value):
    with pytest.raises(ValueError):
        ServiceLifecycle(idle_seconds=value)


@pytest.mark.parametrize("value", ["x" * 129, "job\nsecret", 123])
def test_scheduler_attribution_rejects_unbounded_or_invalid_values(value):
    with pytest.raises(ValueError):
        capture_allocation({"LSB_JOBID": value})


def test_local_service_does_not_invent_scheduler_attribution():
    allocation = capture_allocation({"UNRELATED_SECRET": "secret"})
    assert allocation.job_id is None and allocation.cluster is None and allocation.queue is None
