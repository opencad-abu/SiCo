"""Failure and cancellation contracts for independent GUI worker owners."""

from threading import Event

import pytest

from cadlsf_gui_fixtures import application as application, _snapshot
from cadlsf.collector import CollectorCancelled, CollectorConfig
from cadlsf.gui.job_action_workers import JobActionController, JobActionWorker
from cadlsf.gui.refresh_workers import RefreshController, RefreshWorker
from cadlsf.model import Diagnostic


@pytest.mark.parametrize("cancelled", [False, True])
def test_job_worker_emits_one_terminal_result(application, cancelled):
    class Collector:
        def kill_job(self, job_id):
            assert job_id == "123"
            if cancelled:
                raise CollectorCancelled("cancelled")
            raise ValueError("invalid job")

    worker = JobActionWorker("123", CollectorConfig(), lambda *_: Collector())
    events = []
    worker.signals.succeeded.connect(lambda job: events.append(("success", job)))
    worker.signals.cancelled.connect(lambda job: events.append(("cancelled", job)))
    worker.signals.failed.connect(lambda job, message: events.append((job, message)))
    worker.run()
    expected = ("cancelled", "123") if cancelled else ("123", "ValueError: invalid job")
    assert events == [expected]


@pytest.mark.parametrize("worker_type", [RefreshWorker, JobActionWorker])
def test_cancel_before_start_does_not_create_collector(application, worker_type):
    def factory(*_):
        pytest.fail("cancelled request created a collector")

    worker = (
        RefreshWorker(7, CollectorConfig(), "normal", factory)
        if worker_type is RefreshWorker
        else JobActionWorker("123", CollectorConfig(), factory)
    )
    cancelled = []
    worker.signals.cancelled.connect(cancelled.append)
    worker.cancel()
    worker.run()
    assert cancelled == ([7] if worker_type is RefreshWorker else ["123"])


def test_job_controller_rejects_overlap_and_shutdown_cancels(application):
    entered = Event()
    calls = []

    def factory(_config, cancelled):
        class Collector:
            def kill_job(self, job_id):
                calls.append(job_id)
                entered.set()
                if cancelled.wait(2):
                    raise CollectorCancelled("closed")
                raise RuntimeError("cancellation was not delivered")

        return Collector()

    controller = JobActionController(CollectorConfig(), collector_factory=factory)
    completed = []
    controller.succeeded.connect(lambda job: completed.append(job))
    controller.failed.connect(lambda job, message: completed.append((job, message)))
    try:
        assert controller.kill_job("123")
        assert entered.wait(1)
        assert controller.busy
        assert not controller.kill_job("456")
        assert controller.shutdown()
        application.processEvents()
        assert not controller.busy
        assert calls == ["123"]
        assert completed == []
    finally:
        controller.shutdown()


def test_invalidated_refresh_suppresses_queued_failure(application):
    entered = Event()
    release = Event()

    class Collector:
        def snapshot(self, queue, *, include_jobs):
            entered.set()
            release.wait(2)
            raise ValueError("late failure")

    controller = RefreshController(
        CollectorConfig(), collector_factory=lambda *_: Collector()
    )
    failures = []
    controller.failed.connect(failures.append)
    try:
        token = controller.submit("normal")
        assert entered.wait(1)
        controller.invalidate()
        release.set()
        assert controller.shutdown()
        application.processEvents()
        assert controller.generation > token
        assert not controller.busy
        assert failures == []
    finally:
        release.set()
        controller.shutdown()


def test_refresh_worker_keeps_cached_result_and_topology_diagnostics(application):
    original = _snapshot()
    diagnostic = Diagnostic("topology_failed", "membership unavailable")
    events = []

    class Sample:
        available_host_names = ("node-idle",)

    class Collector:
        def collect_monitor_dynamic_sample(self):
            return Sample()

        def monitor_snapshot(self, cache, queue, *, dynamic_sample):
            return original

        def monitor_topology_refresh(self, cache, hosts, *, force_topology):
            assert force_topology
            assert hosts == ("node-idle",)
            return (diagnostic,)

    worker = RefreshWorker(
        3, CollectorConfig(), "normal", lambda *_: Collector(),
        topology_cache=object(), force_topology=True,
    )
    worker.signals.cached.connect(lambda token, value: events.append(("cached", token, value)))
    worker.signals.completed.connect(lambda token, value: events.append(("final", token, value)))
    worker.run()
    assert [event[:2] for event in events] == [("cached", 3), ("final", 3)]
    assert events[0][2] is original
    assert events[1][2].status == "partial"
    assert events[1][2].diagnostics == (diagnostic,)
    assert original.status == "ready"
    assert original.diagnostics == ()
