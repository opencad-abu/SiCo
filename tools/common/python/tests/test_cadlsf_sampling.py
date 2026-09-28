from __future__ import annotations

import threading
import time
from typing import Sequence


from cadlsf.collector import CommandResult, LsfCollector


from cadlsf.session_topology import SessionTopology
from cadlsf_fixtures import (
    QUEUE_HEADER,
    HOST_HEADER,
    LOAD_HEADER,
    CAPACITY_HEADER,
    JOB_SEPARATOR,
    JOB_FORMAT,
    result as _result,
    commands,
    sampler,
)


def test_membership_discovery_uses_at_most_four_workers() -> None:
    class ConcurrentRunner:
        def __init__(self) -> None:
            self.active = 0
            self.peak = 0
            self.condition = threading.Condition()

        def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
            command = tuple(argv)
            with self.condition:
                self.active += 1
                self.peak = max(self.peak, self.active)
                if self.peak >= 4:
                    self.condition.notify_all()
                else:
                    self.condition.wait_for(lambda: self.peak >= 4, timeout=1)
            time.sleep(0.01)
            with self.condition:
                self.active -= 1
            return _result(
                command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            )

    runner = ConcurrentRunner()
    client = commands(runner)
    topology = SessionTopology(client.queues, client.host_queues)

    memberships, diagnostics = topology.discover(
        [f"node{index:02d}" for index in range(12)]
    )

    assert diagnostics == ()
    assert len(memberships) == 12
    assert runner.peak == 4


def test_host_rows_and_load_are_collected_concurrently() -> None:
    host_command = ("bhosts", "-w")
    load_command = ("lsload", "-I", "r1m:ut:mem:swp")

    class ConcurrentSampleRunner:
        def __init__(self) -> None:
            self.arrived = threading.Barrier(2)
            self.active = 0
            self.peak = 0
            self.lock = threading.Lock()

        def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
            command = tuple(argv)
            assert command in {host_command, load_command}
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                self.arrived.wait(timeout=1)
                if command == host_command:
                    return _result(
                        command,
                        HOST_HEADER + "node01 ok - 8 0 0 0 0 0\n",
                    )
                return _result(
                    command,
                    LOAD_HEADER + "node01 ok 0.0 0.0 0.0 0% 0 0 0 1G 4G 8G\n",
                )
            finally:
                with self.lock:
                    self.active -= 1

    runner = ConcurrentSampleRunner()
    hosts, loads, load_error = sampler(runner).hosts_and_load()

    assert load_error is None
    assert [host.name for host in hosts] == ["node01"]
    assert loads["node01"] == (0.0, 0.0, 8 * 1024**3, 4 * 1024**3)
    assert runner.peak == 2


def test_refresh_queries_queue_host_and_load_concurrently() -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    host_command = ("bhosts", "-w")
    load_command = ("lsload", "-I", "r1m:ut:mem:swp")

    class ConcurrentRefreshRunner:
        def __init__(self) -> None:
            self.arrived = threading.Barrier(3)
            self.active = 0
            self.peak = 0
            self.lock = threading.Lock()

        def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
            command = tuple(argv)
            assert command in {queue_command, host_command, load_command}
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                self.arrived.wait(timeout=1)
                if command == queue_command:
                    return _result(command, QUEUE_HEADER)
                if command == host_command:
                    return _result(command, HOST_HEADER)
                return _result(command, LOAD_HEADER)
            finally:
                with self.lock:
                    self.active -= 1

    runner = ConcurrentRefreshRunner()
    refresh = sampler(runner).refresh()

    assert refresh.queue_error is None
    assert refresh.host_error is None
    assert refresh.load_error is None
    assert runner.peak == 3


def test_monitor_refresh_queries_jobs_concurrently_and_degrades_to_partial() -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    host_command = ("bhosts", "-w")
    load_command = ("lsload", "-I", "r1m:ut:mem:swp")
    capacity_command = ("lshosts", "-w")
    job_command = ("bjobs", "-u", "demo", "-o", JOB_FORMAT, "-noheader")

    class ConcurrentRefreshRunner:
        def __init__(self, fail_jobs: bool = False) -> None:
            self.arrived = threading.Barrier(5)
            self.active = 0
            self.peak = 0
            self.lock = threading.Lock()
            self.fail_jobs = fail_jobs

        def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
            command = tuple(argv)
            assert command in {
                queue_command,
                host_command,
                load_command,
                capacity_command,
                job_command,
            }
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                self.arrived.wait(timeout=1)
                if command == queue_command:
                    return _result(
                        command,
                        QUEUE_HEADER
                        + "normal 30 Open:Active - - - - 0 0 0 0\n",
                    )
                if command == host_command:
                    return _result(command, HOST_HEADER)
                if command == load_command:
                    return _result(command, LOAD_HEADER)
                if command == capacity_command:
                    return _result(command, CAPACITY_HEADER)
                if self.fail_jobs:
                    return _result(command, "", 1)
                return _result(
                    command,
                    JOB_SEPARATOR.join(
                        (
                            "250",
                            "PEND",
                            "normal",
                            "",
                            "",
                            "1",
                            "Aug 20 10:48",
                            "-",
                            "0 second(s)",
                            "pending check",
                        )
                    )
                    + "\n",
                )
            finally:
                with self.lock:
                    self.active -= 1

    runner = ConcurrentRefreshRunner()
    snapshot = LsfCollector(runner=runner, user="demo").snapshot(
        "normal", include_jobs=True
    )

    assert runner.peak == 5
    assert snapshot.status == "ready"
    assert snapshot.jobs[0].user == "demo"
    assert snapshot.jobs[0].name == "pending check"

    failed_runner = ConcurrentRefreshRunner(fail_jobs=True)
    partial = LsfCollector(runner=failed_runner, user="demo").snapshot(
        "normal", include_jobs=True
    )

    assert partial.status == "partial"
    assert partial.queues[0].name == "normal"
    assert partial.jobs == ()
    assert [item.code for item in partial.diagnostics] == [
        "job_collection_failed"
    ]
