from __future__ import annotations
from pathlib import Path
import pytest
from cadlsf.collector import CollectorConfig, CollectorError, CommandResult, LsfCollector
from cadlsf_fixtures import QUEUE_HEADER, JOB_FORMAT, FakeRunner, command_result as _command_result, commands


def test_collect_jobs_treats_no_unfinished_job_as_empty() -> None:
    command = ("bjobs", "-u", "demo", "-o", JOB_FORMAT, "-noheader")
    for stdout, stderr in (
        ("", "No unfinished job found\n"),
        ("No unfinished job found\n", ""),
    ):
        runner = FakeRunner(
            {command: CommandResult(command, 255, stdout, stderr)}
        )

        assert LsfCollector(runner=runner, user="demo").collect_jobs() == ()


def test_kill_job_uses_validated_id_without_a_shell() -> None:
    command = ("site-bkill", "250[3]")
    runner = FakeRunner({command: CommandResult(command, 0, "", "")})
    collector = LsfCollector(
        config=CollectorConfig(bkill="site-bkill"),
        runner=runner,
        user="demo",
    )

    collector.kill_job("250[3]")

    assert runner.calls == [(command, 10.0)]
    for invalid in ("", "250 251", "-J", "250; touch bad", "250[x]"):
        with pytest.raises(CollectorError, match="Invalid LSF job ID"):
            collector.kill_job(invalid)
    assert runner.calls == [(command, 10.0)]


def test_kill_job_reports_lsf_failure() -> None:
    command = ("bkill", "250")
    runner = FakeRunner(
        {command: CommandResult(command, 255, "", "Job <250> is not found\n")}
    )

    with pytest.raises(CollectorError, match=r"bkill failed: Job <250> is not found"):
        LsfCollector(runner=runner, user="demo").kill_job("250")


@pytest.mark.parametrize("stream", ("stdout", "stderr"))
def test_nonmember_host_is_valid_empty_membership(stream: str) -> None:
    command = ("bqueues", "-u", "demo", "-m", "node02", "-w")
    message = "node02: Host or host group is not used by the queue\n"
    result = _command_result(
        command,
        returncode=255,
        **{stream: message},
    )
    client = commands(
        runner=FakeRunner({command: result}), user="demo"
    )

    assert client.host_queues("node02") == ()


@pytest.mark.parametrize(
    "stderr",
    (
        "node02: Bad host name, host group name or cluster name\n",
        (
            "node02: Host or host group is not used by the queue\n"
            "bqueues: batch service is unavailable\n"
        ),
    ),
)
def test_membership_query_preserves_real_lsf_failures(stderr: str) -> None:
    command = ("bqueues", "-u", "demo", "-m", "node02", "-w")
    client = commands(
        runner=FakeRunner(
            {
                command: _command_result(
                    command, returncode=255, stderr=stderr
                )
            }
        ),
        user="demo",
    )

    with pytest.raises(CollectorError, match="bqueues failed"):
        client.host_queues("node02")


def test_lsf_10_1_shell_fixture_accepts_only_supported_argv(tmp_path: Path) -> None:
    bqueues = tmp_path / "bqueues"
    bqueues.write_text(
        """#!/bin/sh
case " $* " in
  *" -n "*|*" -noheader "*|*" -o "*)
    echo "bqueues: illegal option" >&2
    exit 2
    ;;
esac
if [ "$LC_ALL" != C ]; then
  echo "bqueues: locale is not C" >&2
  exit 2
fi
case "$*" in
  "-u demo -w"|"-u demo -m ecs-hz-hpc50 -w")
    printf '%s\n' \
      'QUEUE_NAME PRIO STATUS MAX JL/U JL/P JL/H NJOBS PEND RUN SUSP' \
      'normal 30 Open:Active - - - - 1 0 1 0'
    ;;
  *)
    echo "bqueues: unexpected argv: $*" >&2
    exit 2
    ;;
esac
""",
        encoding="ascii",
    )
    bhosts = tmp_path / "bhosts"
    bhosts.write_text(
        """#!/bin/sh
if [ "$*" != "-w" ]; then
  echo "bhosts: unexpected argv: $*" >&2
  exit 2
fi
printf '%s\n' \
  'HOST_NAME STATUS JL/U MAX NJOBS RUN SSUSP USUSP RSV' \
  'ecs-hz-hpc50 ok - 64 1 1 0 0 0'
""",
        encoding="ascii",
    )
    lsload = tmp_path / "lsload"
    lsload.write_text(
        """#!/bin/sh
if [ "$#" -ne 2 ] || [ "$1" != "-I" ] || [ "$2" != "r1m:ut:mem:swp" ]; then
  echo "lsload: unexpected argv: $*" >&2
  exit 2
fi
printf '%s\n' \
  'HOST_NAME status r15s r1m r15m ut pg ls it tmp swp mem' \
  'ecs-hz-hpc50 ok 0.0 0.0 0.0 0% 0.0 0 1452 1014G 0M 2.8T'
""",
        encoding="ascii",
    )
    for command in (bqueues, bhosts, lsload):
        command.chmod(0o755)

    snapshot = LsfCollector(
        config=CollectorConfig(
            bqueues=str(bqueues),
            bhosts=str(bhosts),
            lsload=str(lsload),
        ),
        user="demo",
    ).snapshot("normal")

    assert snapshot.status == "ready"
    assert [queue.name for queue in snapshot.queues] == ["normal"]
    assert [host.name for host in snapshot.hosts] == ["ecs-hz-hpc50"]
    assert snapshot.hosts[0].load_1m == 0.0
    assert snapshot.hosts[0].memory_available_bytes == int(2.8 * 1024**4)
    assert snapshot.hosts[0].swap_available_bytes == 0


def test_collector_rejects_truncated_lsf_output() -> None:
    command = ("bqueues", "-u", "demo", "-w")
    runner = FakeRunner(
        {
            command: CommandResult(
                command,
                0,
                QUEUE_HEADER + "normal 30 Open:Active - - - - 0 0 0 0\n"
                "\n[output truncated]\n",
                "",
            )
        }
    )

    snapshot = LsfCollector(runner=runner, user="demo").snapshot(
        include_hosts=False
    )

    assert snapshot.status == "error"
    assert snapshot.queues == ()
    assert snapshot.diagnostics[0].code == "queue_collection_failed"
    assert "output exceeded" in snapshot.diagnostics[0].message
