from __future__ import annotations

from pathlib import Path

import pytest

from cadlsf import recommend_hosts
from cadlsf.collector import LsfCollector


from cadlsf_fixtures import (
    QUEUE_HEADER,
    HOST_HEADER,
    LOAD_HEADER,
    CAPACITY_HEADER,
    FakeRunner,
    result as _result,
    command_result as _command_result,
    commands,
    monitor_cache_with_topology as _monitor_cache_with_topology,
    monitor_dynamic_results as _monitor_dynamic_results,
)


def test_monitor_force_reload_refreshes_all_topology_sections(
    tmp_path: Path,
) -> None:
    clock = [1000.0]
    cache = _monitor_cache_with_topology(tmp_path, clock)
    results = _monitor_dynamic_results()
    queue_command = ("bqueues", "-u", "demo", "-w")
    membership_command = (
        "bqueues", "-u", "demo", "-m", "node01", "-w",
    )
    capacity_command = ("lshosts", "-w")
    results.update(
        {
            queue_command: _result(
                queue_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            ),
            membership_command: _result(
                membership_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            ),
            capacity_command: _result(
                capacity_command,
                CAPACITY_HEADER
                + "node01 X86_64 model 1.0 8 64G 16G Yes ()\n",
            ),
        }
    )
    runner = FakeRunner(results)
    collector = LsfCollector(runner=runner, user="demo")
    sample = collector.collect_monitor_dynamic_sample()

    diagnostics = collector.monitor_topology_refresh(
        cache, sample.available_host_names, force_topology=True
    )

    assert diagnostics == ()
    calls = [call for call, _timeout in runner.calls]
    assert calls.count(queue_command) == 1
    assert calls.count(membership_command) == 1
    assert calls.count(capacity_command) == 1
    assert cache.load_host_capacities().value == {
        "node01": 64 * 1024**3
    }


def test_monitor_caches_complete_topology_with_nonmember_host(
    tmp_path: Path,
) -> None:
    clock = [1000.0]
    cache = _monitor_cache_with_topology(tmp_path, clock)
    results = _monitor_dynamic_results()
    host_command = ("bhosts", "-w")
    load_command = ("lsload", "-I", "r1m:ut:mem:swp")
    queue_command = ("bqueues", "-u", "demo", "-w")
    node01_membership = (
        "bqueues", "-u", "demo", "-m", "node01", "-w",
    )
    node02_membership = (
        "bqueues", "-u", "demo", "-m", "node02", "-w",
    )
    capacity_command = ("lshosts", "-w")
    results.update(
        {
            host_command: _result(
                host_command,
                HOST_HEADER
                + "node01 ok - 8 1 1 0 0 0\n"
                + "node02 ok - 8 0 0 0 0 0\n",
            ),
            load_command: _result(
                load_command,
                LOAD_HEADER
                + "node01 ok 0.1 0.2 0.3 25% 0 0 0 1G 8G 24G\n"
                + "node02 ok 0.1 0.2 0.3 10% 0 0 0 1G 12G 24G\n",
            ),
            queue_command: _result(
                queue_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            ),
            node01_membership: _result(
                node01_membership,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            ),
            node02_membership: _command_result(
                node02_membership,
                returncode=255,
                stderr=(
                    "node02: Host or host group is not used by the queue\n"
                ),
            ),
            capacity_command: _result(
                capacity_command,
                CAPACITY_HEADER
                + "node01 X86_64 model 1.0 8 32G 16G Yes ()\n"
                + "node02 X86_64 model 1.0 8 32G 16G Yes ()\n",
            ),
        }
    )
    collector = LsfCollector(runner=FakeRunner(results), user="demo")
    sample = collector.collect_monitor_dynamic_sample()

    diagnostics = collector.monitor_topology_refresh(
        cache, sample.available_host_names, force_topology=True
    )
    snapshot = collector.monitor_snapshot(
        cache, "normal", dynamic_sample=sample
    )

    assert diagnostics == ()
    assert cache.load_host_memberships().value == {
        "node01": ("normal",),
        "node02": (),
    }
    assert [host.name for host in snapshot.hosts] == ["node01"]
    assert [item.host.name for item in recommend_hosts(snapshot.hosts)] == [
        "node01"
    ]


def test_monitor_topology_failure_keeps_stale_cache(tmp_path: Path) -> None:
    clock = [1000.0]
    cache = _monitor_cache_with_topology(tmp_path, clock)
    clock[0] += 8 * 24 * 60 * 60
    dynamic_results = _monitor_dynamic_results()
    queue_command = ("bqueues", "-u", "demo", "-w")
    membership_command = (
        "bqueues", "-u", "demo", "-m", "node01", "-w",
    )
    dynamic_results[queue_command] = _result(queue_command, "", 1)
    dynamic_results[membership_command] = _result(membership_command, "", 1)
    runner = FakeRunner(dynamic_results)
    collector = LsfCollector(runner=runner, user="demo")
    sample = collector.collect_monitor_dynamic_sample()

    diagnostics = collector.monitor_topology_refresh(
        cache, sample.available_host_names
    )
    stale = collector.monitor_snapshot(
        cache, "normal", dynamic_sample=sample
    )

    assert {item.code for item in diagnostics} == {
        "queue_collection_failed",
        "queue_membership_failed",
    }
    assert [host.name for host in stale.hosts] == ["node01"]
    assert cache.load_queues().value is not None
    assert cache.load_host_memberships().value == {"node01": ("normal",)}


@pytest.mark.parametrize(
    ("advance", "expected_commands"),
    (
        (30 * 60 + 1, {"bqueues"}),
        (7 * 24 * 60 * 60 + 1, {"bqueues", "membership"}),
        (
            15 * 24 * 60 * 60 + 1,
            {"bqueues", "membership", "lshosts"},
        ),
    ),
)
def test_monitor_ttl_refreshes_only_expired_sections(
    tmp_path: Path, advance: float, expected_commands: set[str]
) -> None:
    clock = [1000.0]
    cache = _monitor_cache_with_topology(tmp_path, clock)
    clock[0] += advance
    results = _monitor_dynamic_results()
    queue_command = ("bqueues", "-u", "demo", "-w")
    membership_command = (
        "bqueues", "-u", "demo", "-m", "node01", "-w",
    )
    capacity_command = ("lshosts", "-w")
    results.update(
        {
            queue_command: _result(
                queue_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            ),
            membership_command: _result(
                membership_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            ),
            capacity_command: _result(
                capacity_command,
                CAPACITY_HEADER
                + "node01 X86_64 model 1.0 8 32G 16G Yes ()\n",
            ),
        }
    )
    runner = FakeRunner(results)

    diagnostics = LsfCollector(
        runner=runner, user="demo"
    ).monitor_topology_refresh(cache, ("node01",))

    assert diagnostics == ()
    commands = {call[0] for call, _timeout in runner.calls}
    observed = set()
    if "bqueues" in commands:
        observed.add("bqueues")
    if membership_command in [call for call, _timeout in runner.calls]:
        observed.add("membership")
    if "lshosts" in commands:
        observed.add("lshosts")
    assert observed == expected_commands
