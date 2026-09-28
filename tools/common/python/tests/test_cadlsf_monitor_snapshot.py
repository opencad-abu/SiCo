from __future__ import annotations

from pathlib import Path


from cadlsf.collector import LsfCollector


from cadlsf_fixtures import (
    QUEUE_HEADER,
    HOST_HEADER,
    LOAD_HEADER,
    CAPACITY_HEADER,
    FakeRunner,
    result as _result,
    monitor_cache_with_topology as _monitor_cache_with_topology,
    monitor_dynamic_results as _monitor_dynamic_results,
)


def test_monitor_hot_refresh_runs_only_dynamic_commands(tmp_path: Path) -> None:
    clock = [1000.0]
    cache = _monitor_cache_with_topology(tmp_path, clock)
    runner = FakeRunner(_monitor_dynamic_results())
    collector = LsfCollector(runner=runner, user="demo")

    snapshot = collector.monitor_snapshot(cache, "normal")

    assert snapshot.status == "ready"
    assert [host.name for host in snapshot.hosts] == ["node01"]
    assert snapshot.hosts[0].memory_total_bytes == 32 * 1024**3
    assert {call for call, _timeout in runner.calls} == set(
        _monitor_dynamic_results()
    )


def test_monitor_expired_topology_stays_visible_before_refresh(
    tmp_path: Path,
) -> None:
    clock = [1000.0]
    cache = _monitor_cache_with_topology(tmp_path, clock)
    clock[0] += 16 * 24 * 60 * 60
    dynamic_results = _monitor_dynamic_results()
    runner = FakeRunner(dynamic_results)
    collector = LsfCollector(runner=runner, user="demo")
    sample = collector.collect_monitor_dynamic_sample()

    cached = collector.monitor_snapshot(
        cache, "normal", dynamic_sample=sample
    )

    assert [host.name for host in cached.hosts] == ["node01"]
    assert {call for call, _timeout in runner.calls} == set(dynamic_results)


def test_monitor_detects_new_host_and_drops_removed_host(tmp_path: Path) -> None:
    clock = [1000.0]
    cache = _monitor_cache_with_topology(tmp_path, clock)
    dynamic_results = _monitor_dynamic_results()
    host_command = ("bhosts", "-w")
    dynamic_results[host_command] = _result(
        host_command,
        HOST_HEADER + "node02 ok - 16 0 0 0 0 0\n",
    )
    load_command = ("lsload", "-I", "r1m:ut:mem:swp")
    dynamic_results[load_command] = _result(
        load_command,
        LOAD_HEADER + "node02 ok 0.1 0.1 0.2 5% 0 0 0 1G 16G 32G\n",
    )
    membership_command = (
        "bqueues", "-u", "demo", "-m", "node02", "-w",
    )
    capacity_command = ("lshosts", "-w")
    dynamic_results[membership_command] = _result(
        membership_command,
        QUEUE_HEADER + "normal 30 Open:Active - - - - 0 0 0 0\n",
    )
    dynamic_results[capacity_command] = _result(
        capacity_command,
        CAPACITY_HEADER
        + "node02 X86_64 model 1.0 16 64G 16G Yes ()\n",
    )
    runner = FakeRunner(dynamic_results)
    collector = LsfCollector(runner=runner, user="demo")
    sample = collector.collect_monitor_dynamic_sample()

    before = collector.monitor_snapshot(
        cache, "normal", dynamic_sample=sample
    )
    assert before.hosts == ()
    diagnostics = collector.monitor_topology_refresh(
        cache, sample.available_host_names
    )
    after = collector.monitor_snapshot(
        cache, "normal", dynamic_sample=sample
    )

    assert diagnostics == ()
    assert [host.name for host in after.hosts] == ["node02"]
    assert "node01" not in {host.name for host in after.hosts}
    assert membership_command in [call for call, _timeout in runner.calls]
