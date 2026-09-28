"""Independent component contracts after removing the collector's shared host state."""

from threading import Event
import threading

import pytest

from cadlsf import collector
from cadlsf.command_runner import CollectorCancelled, CollectorError
from cadlsf.commands import CollectorConfig
from cadlsf.model import HostInfo, QueueInfo
from cadlsf.monitor_topology import MonitorTopology
from cadlsf.sampling import LsfSampler, MonitorDynamicSample, RefreshData
from cadlsf.session_topology import SessionTopology
from cadlsf.snapshots import merge_hosts, monitor_snapshot
from cadlsf_fixtures import monitor_cache_with_topology


def test_collector_compatibility_exports_share_their_owners():
    from cadlsf import capacity, command_runner, commands, parsers

    for name in ("CollectorError", "CollectorCancelled", "CommandResult", "SubprocessRunner"):
        assert getattr(collector, name) is getattr(command_runner, name)
    assert collector.CollectorConfig is commands.CollectorConfig
    assert collector.HostCapacityCache is capacity.HostCapacityCache
    assert collector.parse_hosts is parsers.parse_hosts
    assert collector.MonitorDynamicSample is MonitorDynamicSample


@pytest.mark.parametrize("operation", ["refresh", "monitor_dynamic", "hosts_and_load"])
def test_sampler_propagates_cancellation_and_waits_for_inflight_reads(operation):
    finished = Event()
    started = Event()
    release = Event()

    def cancel():
        assert started.wait(1), "load sample did not start concurrently"
        release.set()
        raise CollectorCancelled("cancel fixture")

    def load():
        started.set()
        assert release.wait(1), "host sample did not release the load reader"
        finished.set()
        return {}

    sampler = LsfSampler(queues=lambda: ((), ()), hosts=cancel, load=load,
                         capacities=lambda: {}, jobs=lambda: ())
    with pytest.raises(CollectorCancelled, match="cancel fixture"):
        getattr(sampler, operation)()
    # Running requests are joined before the cancellation leaves the component.
    assert finished.is_set()
    assert not any(
        thread.name.startswith("cad-lsf-") and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_sampler_retains_independent_failures_without_retrying():
    calls = []

    def fail(name):
        calls.append(name)
        raise CollectorError(name)

    sampler = LsfSampler(queues=lambda: fail("queues"), hosts=lambda: fail("hosts"),
                         load=lambda: fail("load"), capacities=lambda: fail("capacity"),
                         jobs=lambda: fail("jobs"))
    sample = sampler.refresh(include_jobs=True)
    assert sorted(calls) == ["capacity", "hosts", "jobs", "load", "queues"]
    assert str(sample.queue_error) == "queues" and str(sample.host_error) == "hosts"
    assert str(sample.load_error) == "load" and str(sample.job_error) == "jobs"
    assert sample.capacities == {}  # Historical capacity failure is not a fatal host failure.


def test_session_topology_accepts_narrow_callbacks_and_preserves_queue_diagnostic():
    rows = (QueueInfo("normal", "Open:Active", True), QueueInfo("closed", "Closed", False))

    class Cache:
        def load_queue_names(self):
            return None

        def store_queue_names(self, names):
            assert names == ["normal", "closed"]
            return False

    topology = SessionTopology(lambda: rows, lambda _: (), Cache())
    queues, diagnostics = topology.queues()
    assert queues == rows[:1]
    assert diagnostics[0].code == "topology_cache_write_failed"


def test_monitor_cancellation_cannot_publish_partial_membership(tmp_path):
    clock = [1000.0]
    cache = monitor_cache_with_topology(tmp_path, clock)
    old = cache.load_host_memberships().value

    def cancelled(_):
        raise CollectorCancelled("stop membership refresh")

    topology = MonitorTopology(queues=lambda: (), host_queues=cancelled, capacities=lambda: {})
    # Only membership needs refreshing because node02 has no previous membership.
    with pytest.raises(CollectorCancelled):
        topology.refresh(cache, ["node01", "node02"])
    assert cache.load_host_memberships().value == old


def test_snapshot_assembly_retains_stale_evidence_and_input_identity():
    queue = QueueInfo("normal", "Open:Active", True)
    host = HostInfo("node01", "ok", True, max_slots=8, running_jobs=2)
    sample = RefreshData(hosts=(host,), loads={"node01": (0.5, 0.25, 16, 8)})
    memberships = {"node01": ("normal",)}
    snapshot = monitor_snapshot(
        user="demo", queue="normal", requested="normal", dynamic=sample,
        collected_at="captured-time", queues=(queue,), memberships=memberships,
        capacities={"node01": 32},
    )
    assert snapshot.collected_at == "captured-time" and snapshot.queues[0] is queue
    assert snapshot.hosts[0].load_1m == 0.5 and snapshot.hosts[0].memory_total_bytes == 32
    assert memberships == {"node01": ("normal",)} and sample.hosts[0] is host
    assert host.load_1m is None  # Assembly cannot mutate source observations.


def test_session_and_monitor_keep_distinct_missing_load_semantics():
    host = HostInfo("node01", "ok", True, load_1m=2.5, cpu_utilization=0.75)
    arguments = ("normal", (host,), {}, CollectorError("load failed"), {"node01": ("normal",)}, {})
    session_hosts, diagnostics = merge_hosts(*arguments, preserve_missing_load=True)
    monitor_hosts, _ = merge_hosts(*arguments)
    assert session_hosts[0].load_1m == 2.5 and monitor_hosts[0].load_1m is None
    assert diagnostics[0].code == "load_collection_failed"
    assert "memory_total_bytes" in monitor_hosts[0].unavailable_metrics


def test_invalid_session_queue_never_starts_commands():
    class Runner:
        def run(self, *args, **kwargs):
            pytest.fail("invalid queue started a command")

    result = collector.LsfCollector(config=CollectorConfig(), runner=Runner(), user="demo").snapshot("bad queue")
    assert result.status == "error" and result.diagnostics[0].code == "invalid_queue"
