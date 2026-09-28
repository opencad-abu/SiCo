from __future__ import annotations


import pytest

from cadlsf import JobInfo, QueueInfo
from cadlsf.collector import (
    CollectorError,
    parse_hosts,
    parse_host_capacities,
    parse_jobs,
    parse_load,
    parse_queues,
)


from cadlsf_fixtures import QUEUE_HEADER, HOST_HEADER, LOAD_HEADER, CAPACITY_HEADER, JOB_SEPARATOR


def test_parse_queues_filters_closed_and_preserves_job_counts() -> None:
    queues = parse_queues(
        QUEUE_HEADER
        + "normal 30 Open:Active - - - - 12 3 9 0\n"
        "closed 20 Closed:Inact - - - - 4 4 0 0\n"
        "batch 10 Open:Active - - - - 2 0 2 0\n",
    )

    assert queues == (
        QueueInfo("normal", "Open:Active", True, 12, 3, 9),
        QueueInfo("closed", "Closed:Inact", False, 4, 4, 0),
        QueueInfo("batch", "Open:Active", True, 2, 0, 2),
    )


def test_parse_load_normalizes_threshold_cpu_and_memory_units() -> None:
    loads = parse_load(
        LOAD_HEADER
        + "node01 ok 0.1 *1.5 2.0 25% 0 0 0 1G 512M 16G\n"
        "node02 ok - - - - - - - - - -\n",
    )

    assert loads["node01"] == (1.5, 0.25, 16 * 1024**3, 512 * 1024**2)
    assert loads["node02"] == (None, None, None, None)


def test_parse_real_lsf_10_1_load_row() -> None:
    loads = parse_load(
        LOAD_HEADER
        + "ecs-hz-hpc50 ok 0.0 0.0 0.0 0% 0.0 0 1452 1014G 0M 2.8T\n"
    )

    assert loads["ecs-hz-hpc50"] == (
        0.0,
        0.0,
        int(2.8 * 1024**4),
        0,
    )


def test_parse_real_lsf_10_1_host_capacities() -> None:
    capacities = parse_host_capacities(
        CAPACITY_HEADER
        + "work-srv X86_64 Opteron848 60.0 32 503G 63.9G Yes (mg)\n"
        "node01 X86_64 Opteron848 60.0 2 3.5G 3.9G Yes ()\n"
        "legacy X86_64 Opteron848 60.0 2 4096 4096 Yes ()\n"
        "unknown X86_64 unknown 1.0 1 - - Yes ()\n"
    )

    assert capacities == {
        "work-srv": 503 * 1024**3,
        "node01": int(3.5 * 1024**3),
        "legacy": 4096 * 1024,
        "unknown": None,
    }


def test_parse_lsf_10_1_jobs_preserves_spaces_empty_hosts_and_runtime() -> None:
    jobs = parse_jobs(
        JOB_SEPARATOR.join(
            (
                "250",
                "RUN",
                "owners",
                "lsf-node2",
                "lsf-node2:lsf-node3",
                "2",
                "Aug 20 10:48",
                "Aug 20 10:49",
                "75 second(s)",
                "calibre run with spaces",
            )
        )
        + "\n"
        + JOB_SEPARATOR.join(
            (
                "251",
                "PEND",
                "normal",
                "",
                "",
                "4",
                "Aug 20 10:50",
                "-",
                "",
                "waiting job",
            )
        )
        + "\n"
    )

    assert jobs == (
        JobInfo(
            "250",
            "",
            "RUN",
            "owners",
            "lsf-node2",
            "lsf-node2:lsf-node3",
            2,
            "Aug 20 10:48",
            "Aug 20 10:49",
            75,
            "calibre run with spaces",
        ),
        JobInfo(
            "251",
            "",
            "PEND",
            "normal",
            "",
            "",
            4,
            "Aug 20 10:50",
            "-",
            None,
            "waiting job",
        ),
    )
    assert parse_jobs("") == ()
    assert parse_jobs("No unfinished job found\n") == ()
    with pytest.raises(CollectorError, match="bjobs returned malformed output"):
        parse_jobs("250 RUN normal truncated\n")
    with pytest.raises(CollectorError, match="bjobs returned malformed output"):
        parse_jobs(
            JOB_SEPARATOR.join(
                (
                    "252",
                    "RUN",
                    "normal",
                    "node01",
                    "node01",
                    "1",
                    "Aug 20 11:00",
                    "Aug 20 11:00",
                    "1 second(s)",
                    f"bad{JOB_SEPARATOR}name",
                )
            )
            + "\n"
        )


def test_parsers_reject_missing_headers_and_mark_missing_metrics() -> None:
    with pytest.raises(CollectorError, match="bqueues returned malformed output"):
        parse_queues("not-a-table\n")
    with pytest.raises(CollectorError, match="bhosts returned malformed output"):
        parse_hosts("node-without-status\n")
    with pytest.raises(CollectorError, match="lsload returned malformed output"):
        parse_load("node-without-load\n")

    hosts = parse_hosts(HOST_HEADER + "node01 ok - 16 - 2 - - -\n")
    loads = parse_load(
        LOAD_HEADER + "node01 ok - - - N/A - - - - - broken\n"
    )

    assert hosts[0].running_jobs == 2
    assert {
        "total_jobs",
        "suspended_jobs",
        "reserved_slots",
    }.issubset(hosts[0].unavailable_metrics)
    assert loads["node01"] == (None, None, None, None)
