from __future__ import annotations

from pathlib import Path
from typing import Sequence


from cadlsf import QueueInfo
from cadlsf.cache import MonitorTopologyCache
from cadlsf.commands import CollectorConfig, LsfCommands
from cadlsf.command_runner import CommandResult
from cadlsf.sampling import LsfSampler
from cadlsf.session_topology import SessionTopology


CAD_ROOT = Path(__file__).resolve().parents[3]
QUEUE_HEADER = "QUEUE_NAME PRIO STATUS MAX JL/U JL/P JL/H NJOBS PEND RUN SUSP\n"
HOST_HEADER = "HOST_NAME STATUS JL/U MAX NJOBS RUN SSUSP USUSP RSV\n"
LOAD_HEADER = "HOST_NAME status r15s r1m r15m ut pg ls it tmp swp mem\n"
CAPACITY_HEADER = "HOST_NAME type model cpuf ncpus maxmem maxswp server RESOURCES\n"
JOB_SEPARATOR = "\x1f"
JOB_FORMAT = (
    "jobid stat queue first_host exec_host slots submit_time start_time "
    f'run_time job_name delimiter="{JOB_SEPARATOR}"'
)


class FakeRunner:
    def __init__(self, results: dict[tuple[str, ...], CommandResult]) -> None:
        self.results = results
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
        command = tuple(argv)
        self.calls.append((command, timeout))
        try:
            return self.results[command]
        except KeyError as exc:
            raise AssertionError(f"Unexpected command: {command!r}") from exc


def result(argv: tuple[str, ...], stdout: str, returncode: int = 0) -> CommandResult:
    return CommandResult(argv, returncode, stdout, "failed\n" if returncode else "")


def command_result(
    argv: tuple[str, ...],
    *,
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> CommandResult:
    return CommandResult(argv, returncode, stdout, stderr)


def commands(runner, *, config=None, user="demo"):
    return LsfCommands(config or CollectorConfig(), runner, user)


def sampler(runner):
    client = commands(runner)
    topology = SessionTopology(client.queues, client.host_queues)
    return LsfSampler(queues=topology.queues, hosts=client.hosts, load=client.load,
                      capacities=client.capacities, jobs=client.jobs)


def monitor_cache_with_topology(
    tmp_path: Path,
    clock: list[float],
) -> MonitorTopologyCache:
    cache = MonitorTopologyCache(
        tmp_path / ".cad" / "lsf" / "1001",
        user="demo",
        command_signature=("bqueues", "bhosts", "lsload", "bjobs", "lshosts"),
        environment_identity=("cluster=test",),
        clock=lambda: clock[0],
    )
    assert cache.store_queues(
        (QueueInfo("normal", "Open:Active", True, 1, 0, 1),)
    )
    assert cache.store_host_memberships({"node01": ("normal",)})
    assert cache.store_host_capacities({"node01": 32 * 1024**3})
    return cache


def monitor_dynamic_results() -> dict[tuple[str, ...], CommandResult]:
    host_command = ("bhosts", "-w")
    load_command = ("lsload", "-I", "r1m:ut:mem:swp")
    job_command = ("bjobs", "-u", "demo", "-o", JOB_FORMAT, "-noheader")
    return {
        host_command: result(
            host_command,
            HOST_HEADER + "node01 ok - 8 1 1 0 0 0\n",
        ),
        load_command: result(
            load_command,
            LOAD_HEADER + "node01 ok 0.1 0.2 0.3 25% 0 0 0 1G 8G 24G\n",
        ),
        job_command: result(job_command, ""),
    }
