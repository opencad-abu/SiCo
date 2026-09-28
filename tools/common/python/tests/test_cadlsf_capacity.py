from __future__ import annotations

from typing import Sequence

import pytest

from cadlsf import HostCapacityCache
from cadlsf.collector import CollectorConfig, CollectorError, CommandResult


from cadlsf.capacity import CapacityReader
from cadlsf_fixtures import CAPACITY_HEADER, FakeRunner, result as _result, commands


def test_host_capacity_cache_reuses_the_first_static_sample() -> None:
    cache = HostCapacityCache()

    assert cache.load() is None
    assert cache.store({"node01": 32 * 1024**3}) == {
        "node01": 32 * 1024**3
    }
    assert cache.store({"node01": 64 * 1024**3}) == {
        "node01": 32 * 1024**3
    }
    assert cache.load() == {"node01": 32 * 1024**3}


def test_monitor_merges_memory_capacity_and_reuses_static_query() -> None:
    capacity_command = ("site-lshosts", "-w")

    class CapacityRunner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, ...]] = []

        def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
            command = tuple(argv)
            assert command == capacity_command
            self.calls.append(command)
            return _result(
                command,
                CAPACITY_HEADER
                + "node01 X86_64 model 1.0 8 32G 16G Yes ()\n",
            )

    runner = CapacityRunner()
    cache = HostCapacityCache()
    reader = CapacityReader(commands(runner, config=CollectorConfig(lshosts="site-lshosts")).capacities, cache)

    first = reader.collect()
    second = reader.collect()

    assert first == second == {"node01": 32 * 1024**3}
    assert runner.calls == [capacity_command]


def test_failed_host_capacity_query_is_not_repeated() -> None:
    capacity_command = ("lshosts", "-w")
    runner = FakeRunner(
        {capacity_command: _result(capacity_command, "", returncode=1)}
    )
    reader = CapacityReader(commands(runner).capacities, HostCapacityCache())

    with pytest.raises(CollectorError, match="lshosts failed"):
        reader.collect()
    assert reader.collect() == {}
    assert [command for command, _timeout in runner.calls] == [
        capacity_command
    ]
