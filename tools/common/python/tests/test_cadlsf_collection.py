from __future__ import annotations
from pathlib import Path
import pytest
from cadlsf.cache import SessionTopologyCache
from cadlsf.collector import LsfCollector
from cadlsf_fixtures import QUEUE_HEADER, HOST_HEADER, LOAD_HEADER, FakeRunner, result as _result


def test_collector_builds_permission_filtered_load_sorted_snapshot() -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    host_command = ("bhosts", "-w")
    load_command = ("lsload", "-I", "r1m:ut:mem:swp")
    membership = lambda host: (
        "bqueues",
        "-u",
        "demo",
        "-m",
        host,
        "-w",
    )
    results = {
        queue_command: _result(
            queue_command,
            QUEUE_HEADER
            + "normal 30 Open:Active - - - - 20 4 16 0\n"
            "closed 20 Closed:Inact - - - - 0 0 0 0\n",
        ),
        host_command: _result(
            host_command,
            HOST_HEADER
            + "node01 ok - 16 12 12 0 0 0\n"
            "node02 ok - 16 2 2 0 0 0\n"
            "node03 closed - 16 0 0 0 0 0\n",
        ),
        membership("node01"): _result(
            membership("node01"),
            QUEUE_HEADER + "normal 30 Open:Active - - - - 20 4 16 0\n",
        ),
        membership("node02"): _result(
            membership("node02"),
            QUEUE_HEADER + "other 20 Open:Active - - - - 1 0 1 0\n",
        ),
        membership("node03"): _result(
            membership("node03"),
            QUEUE_HEADER + "normal 30 Open:Active - - - - 20 4 16 0\n",
        ),
        load_command: _result(
            load_command,
            LOAD_HEADER + "node01 ok 0.1 3.0 2.0 50% 0 0 0 1G 2G 8G\n",
        ),
    }
    runner = FakeRunner(results)
    collector = LsfCollector(runner=runner, user="demo")

    snapshot = collector.snapshot("normal")

    assert snapshot.status == "ready"
    assert snapshot.user == "demo"
    assert [queue.name for queue in snapshot.queues] == ["normal"]
    assert [host.name for host in snapshot.hosts] == ["node01"]
    assert snapshot.hosts[0].cpu_utilization == 0.5
    assert all(timeout == 10.0 for _, timeout in runner.calls)


def test_live_queue_result_is_not_hidden_by_session_cache(tmp_path: Path) -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    cache = SessionTopologyCache(
        tmp_path / ".cad" / "lsf",
        session_pid=1234,
        session_start_time="5678",
        user="demo",
        command_signature=("bqueues", "bhosts", "lsload"),
    )
    assert cache.store_queue_names(("normal",))
    runner = FakeRunner(
        {
            queue_command: _result(
                queue_command,
                QUEUE_HEADER
                + "normal 30 Open:Active - - - - 0 0 0 0\n"
                "newqueue 20 Open:Active - - - - 0 0 0 0\n",
            )
        }
    )

    queues = LsfCollector(
        runner=runner, user="demo", topology_cache=cache
    ).collect_queues()

    assert [queue.name for queue in queues] == ["normal", "newqueue"]


def test_queue_cache_write_failure_preserves_live_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    runner = FakeRunner(
        {
            queue_command: _result(
                queue_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 0 0 0 0\n",
            )
        }
    )
    cache = SessionTopologyCache(
        tmp_path / ".cad" / "lsf",
        session_pid=1234,
        session_start_time="5678",
        user="demo",
        command_signature=("bqueues", "bhosts", "lsload"),
    )
    monkeypatch.setattr(cache, "store_queue_names", lambda _queues: False)

    snapshot = LsfCollector(
        runner=runner, user="demo", topology_cache=cache
    ).snapshot(include_hosts=False)

    assert snapshot.status == "partial"
    assert [queue.name for queue in snapshot.queues] == ["normal"]
    assert snapshot.diagnostics[0].code == "topology_cache_write_failed"
    assert snapshot.diagnostics[0].message == (
        "Cannot cache accessible LSF queues; they will be queried again on "
        "the next refresh"
    )


def test_collector_does_not_retry_failed_membership() -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    host_command = ("bhosts", "-w")
    membership_command = (
        "bqueues", "-u", "demo", "-m", "node01", "-w",
    )
    load_command = ("lsload", "-I", "r1m:ut:mem:swp")
    runner = FakeRunner(
        {
            queue_command: _result(
                queue_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 0 0 0 0\n",
            ),
            host_command: _result(
                host_command, HOST_HEADER + "node01 ok - 8 0 0 0 0 0\n"
            ),
            membership_command: _result(membership_command, "", 1),
            load_command: _result(
                load_command,
                LOAD_HEADER + "node01 ok 0.1 0.1 0.1 5% 0 0 0 1G 4G 8G\n",
            ),
        }
    )

    snapshot = LsfCollector(runner=runner, user="demo").snapshot("normal")

    assert snapshot.hosts == ()
    assert [item.code for item in snapshot.diagnostics] == [
        "queue_membership_failed"
    ]
    assert [command for command, _ in runner.calls].count(membership_command) == 1


def test_snapshot_preserves_verified_hosts_when_load_collection_fails() -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    host_command = ("bhosts", "-w")
    membership_command = (
        "bqueues", "-u", "demo", "-m", "node01", "-w",
    )
    load_command = ("lsload", "-I", "r1m:ut:mem:swp")
    runner = FakeRunner(
        {
            queue_command: _result(
                queue_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            ),
            host_command: _result(
                host_command, HOST_HEADER + "node01 ok - 8 1 1 0 0 0\n"
            ),
            membership_command: _result(
                membership_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 1 0 1 0\n",
            ),
            load_command: _result(load_command, "", 1),
        }
    )

    snapshot = LsfCollector(runner=runner, user="demo").snapshot("normal")

    assert snapshot.status == "partial"
    assert [host.name for host in snapshot.hosts] == ["node01"]
    assert {
        "load_1m",
        "cpu_utilization",
        "memory_available_bytes",
        "swap_available_bytes",
    }.issubset(snapshot.hosts[0].unavailable_metrics)
    assert [item.code for item in snapshot.diagnostics] == ["load_collection_failed"]
    assert [command for command, _ in runner.calls].count(load_command) == 1


def test_snapshot_rejects_unavailable_or_invalid_explicit_queue() -> None:
    queue_command = ("bqueues", "-u", "demo", "-w")
    host_command = ("bhosts", "-w")
    load_command = ("lsload", "-I", "r1m:ut:mem:swp")
    runner = FakeRunner(
        {
            queue_command: _result(
                queue_command,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 0 0 0 0\n",
            ),
            host_command: _result(host_command, HOST_HEADER),
            load_command: _result(load_command, LOAD_HEADER),
        }
    )
    collector = LsfCollector(runner=runner, user="demo")

    unavailable = collector.snapshot("secret")
    invalid = collector.snapshot("bad queue")

    assert unavailable.status == "error"
    assert unavailable.selected_queue is None
    assert unavailable.diagnostics[0].code == "queue_not_accessible"
    assert invalid.status == "error"
    assert invalid.diagnostics[0].code == "invalid_queue"
    assert [command for command, _ in runner.calls].count(host_command) == 1
    assert [command for command, _ in runner.calls].count(load_command) == 1


def test_snapshot_reports_malformed_command_output() -> None:
    command = ("bqueues", "-u", "demo", "-w")
    runner = FakeRunner({command: _result(command, "malformed\n")})

    snapshot = LsfCollector(runner=runner, user="demo").snapshot(include_hosts=False)

    assert snapshot.status == "error"
    assert snapshot.queues == ()
    assert snapshot.diagnostics[0].code == "queue_collection_failed"
    assert snapshot.diagnostics[0].message == "bqueues returned malformed output"
    assert [called for called, _ in runner.calls] == [command]
