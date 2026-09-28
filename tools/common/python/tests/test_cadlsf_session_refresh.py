from __future__ import annotations
from pathlib import Path
import threading
from typing import Sequence
from cadlsf.cache import SessionTopologyCache
from cadlsf.collector import CommandResult, LsfCollector
from cadlsf_fixtures import QUEUE_HEADER, HOST_HEADER, LOAD_HEADER, FakeRunner, result as _result


def test_session_cache_builds_all_queue_memberships_once_and_refreshes_load(
    tmp_path: Path,
) -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    host_command = ("bhosts", "-w")
    node01_membership = (
        "bqueues", "-u", "demo", "-m", "node01", "-w",
    )
    node02_membership = (
        "bqueues", "-u", "demo", "-m", "node02", "-w",
    )
    load_command = ("lsload", "-I", "r1m:ut:mem:swp")
    runner = FakeRunner(
        {
            queue_command: _result(
                queue_command,
                QUEUE_HEADER
                + "normal 30 Open:Active - - - - 1 0 1 0\n"
                "batch 20 Open:Active - - - - 1 0 1 0\n",
            ),
            host_command: _result(
                host_command,
                HOST_HEADER
                + "node01 ok - 8 1 1 0 0 0\n"
                "node02 ok - 8 0 0 0 0 0\n",
            ),
            node01_membership: _result(
                node01_membership,
                QUEUE_HEADER
                + "normal 30 Open:Active - - - - 1 0 1 0\n"
                "batch 20 Open:Active - - - - 1 0 1 0\n",
            ),
            node02_membership: _result(
                node02_membership,
                QUEUE_HEADER + "batch 20 Open:Active - - - - 1 0 1 0\n",
            ),
            load_command: _result(
                load_command,
                LOAD_HEADER
                + "node01 ok 0.1 0.2 0.3 5% 0 0 0 1G 4G 8G\n"
                "node02 ok 0.1 0.1 0.2 2% 0 0 0 1G 8G 16G\n",
            ),
        }
    )
    cache = SessionTopologyCache(
        tmp_path / ".cad" / "lsf",
        session_pid=1234,
        session_start_time="5678",
        user="demo",
        command_signature=("bqueues", "bhosts", "lsload"),
    )
    collector = LsfCollector(
        runner=runner, user="demo", topology_cache=cache
    )

    first = collector.snapshot("normal")
    second = collector.snapshot("batch")

    assert [host.name for host in first.hosts] == ["node01"]
    assert {host.name for host in second.hosts} == {"node01", "node02"}
    calls = [command for command, _ in runner.calls]
    assert calls.count(queue_command) == 2
    assert calls.count(node01_membership) == 1
    assert calls.count(node02_membership) == 1
    assert calls.count(host_command) == 2
    assert calls.count(load_command) == 2
    assert cache.load_host_memberships() == {
        "node01": ("normal", "batch"),
        "node02": ("batch",),
    }
    assert cache.queue_path.stat().st_mode & 0o777 == 0o600
    assert cache.membership_path("node01").stat().st_mode & 0o777 == 0o600
    assert cache.membership_path("node02").stat().st_mode & 0o777 == 0o600


def test_session_cache_membership_ttl_refreshes_changed_topology(
    tmp_path: Path,
) -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    host_command = ("bhosts", "-w")
    load_command = ("lsload", "-I", "r1m:ut:mem:swp")
    membership_command = (
        "bqueues", "-u", "demo", "-m", "node01", "-w",
    )
    clock = [100.0]

    class ChangingTopologyRunner(FakeRunner):
        def __init__(self) -> None:
            super().__init__(
                {
                    queue_command: _result(
                        queue_command,
                        QUEUE_HEADER
                        + "normal 30 Open:Active - - - - 0 0 0 0\n"
                        + "batch 20 Open:Active - - - - 0 0 0 0\n",
                    ),
                    host_command: _result(
                        host_command,
                        HOST_HEADER + "node01 ok - 8 0 0 0 0 0\n",
                    ),
                    load_command: _result(
                        load_command,
                        LOAD_HEADER
                        + "node01 ok 0.1 0.1 0.2 2% 0 0 0 1G 8G 16G\n",
                    ),
                }
            )
            self.membership_calls = 0

        def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
            command = tuple(argv)
            if command != membership_command:
                return super().run(argv, timeout=timeout)
            self.calls.append((command, timeout))
            self.membership_calls += 1
            queue = "normal" if self.membership_calls == 1 else "batch"
            return _result(
                command,
                QUEUE_HEADER
                + f"{queue} 30 Open:Active - - - - 0 0 0 0\n",
            )

    cache = SessionTopologyCache(
        tmp_path / ".cad" / "lsf",
        session_pid=1234,
        session_start_time="5678",
        user="demo",
        command_signature=("bqueues", "bhosts", "lsload"),
        membership_ttl=5.0,
        clock=lambda: clock[0],
    )
    runner = ChangingTopologyRunner()
    collector = LsfCollector(runner=runner, user="demo", topology_cache=cache)

    first = collector.snapshot("normal")
    clock[0] = 104.0
    cached_switch = collector.snapshot("batch")
    clock[0] = 106.0
    refreshed_switch = collector.snapshot("batch")

    assert [host.name for host in first.hosts] == ["node01"]
    assert cached_switch.hosts == ()
    assert [host.name for host in refreshed_switch.hosts] == ["node01"]
    assert runner.membership_calls == 2


def test_session_cache_retries_only_failed_membership_hosts(tmp_path: Path) -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    host_command = ("bhosts", "-w")
    load_command = ("lsload", "-I", "r1m:ut:mem:swp")
    node01_membership = (
        "bqueues", "-u", "demo", "-m", "node01", "-w",
    )
    node02_membership = (
        "bqueues", "-u", "demo", "-m", "node02", "-w",
    )

    class RetryRunner(FakeRunner):
        def __init__(self) -> None:
            common = {
                queue_command: _result(
                    queue_command,
                    QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
                ),
                host_command: _result(
                    host_command,
                    HOST_HEADER
                    + "node01 ok - 8 1 1 0 0 0\n"
                    "node02 ok - 8 0 0 0 0 0\n",
                ),
                node01_membership: _result(
                    node01_membership,
                    QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
                ),
                load_command: _result(
                    load_command,
                    LOAD_HEADER
                    + "node01 ok 0.1 0.2 0.3 5% 0 0 0 1G 4G 8G\n"
                    "node02 ok 0.1 0.1 0.2 2% 0 0 0 1G 8G 16G\n",
                ),
            }
            super().__init__(common)
            self._node02_attempts = 0
            self._lock = threading.Lock()

        def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
            command = tuple(argv)
            if command != node02_membership:
                return super().run(argv, timeout=timeout)
            with self._lock:
                self.calls.append((command, timeout))
                self._node02_attempts += 1
                if self._node02_attempts == 1:
                    return _result(command, "", 1)
            return _result(
                command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            )

    cache = SessionTopologyCache(
        tmp_path / ".cad" / "lsf",
        session_pid=1234,
        session_start_time="5678",
        user="demo",
        command_signature=("bqueues", "bhosts", "lsload"),
    )
    runner = RetryRunner()
    collector = LsfCollector(runner=runner, user="demo", topology_cache=cache)

    first = collector.snapshot("normal")
    second = collector.snapshot("normal")

    assert first.status == "partial"
    assert [host.name for host in first.hosts] == ["node01"]
    assert [item.code for item in first.diagnostics] == [
        "queue_membership_failed"
    ]
    assert second.status == "ready"
    assert {host.name for host in second.hosts} == {"node01", "node02"}
    calls = [command for command, _ in runner.calls]
    assert calls.count(node01_membership) == 1
    assert calls.count(node02_membership) == 2
    assert cache.load_host_memberships() == {
        "node01": ("normal",),
        "node02": ("normal",),
    }
