"""LSF command vocabulary, permissions and argument validation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import pwd
import re
from typing import Sequence

from .command_runner import (CollectorError, CommandResult, CommandRunner,
                             STDOUT_LIMIT_BYTES, TRUNCATION_TEXT)
from .model import HostInfo, JobInfo, QueueInfo
from .parsers import (BJOBS_FORMAT, parse_queues, parse_hosts, parse_load,
                      parse_host_capacities, parse_queue_names, parse_jobs)

_JOB_ID = re.compile(r"^[0-9]+(?:\[[0-9]+\])?$")
_RESOURCE_NAME = re.compile(r"^[A-Za-z0-9_.:@/+]+(?:-[A-Za-z0-9_.:@/+]+)*$")
_LOAD_INDICES = "r1m:ut:mem:swp"
_EMPTY_MEMBERSHIP_MESSAGE = "host or host group is not used by the queue"


@dataclass(frozen=True)
class CollectorConfig:
    bqueues: str = "bqueues"
    bhosts: str = "bhosts"
    lsload: str = "lsload"
    bjobs: str = "bjobs"
    bkill: str = "bkill"
    timeout: float = 10.0
    lshosts: str = "lshosts"


def current_os_user() -> str:
    return pwd.getpwuid(os.geteuid()).pw_name


def validate_resource_name(value: str, kind: str) -> str:
    if not isinstance(value, str) or not _RESOURCE_NAME.fullmatch(value):
        raise CollectorError(f"Invalid LSF {kind} name")
    return value


def _is_empty_membership_result(result: CommandResult) -> bool:
    """Recognize LSF's nonzero response for a valid empty -m query."""
    if result.returncode == 0:
        return False
    lines = [
        line.strip()
        for stream in (result.stdout, result.stderr)
        for line in stream.splitlines()
        if line.strip()
    ]
    if not lines:
        return False
    for line in lines:
        message = line[:-1] if line.endswith(".") else line
        if message.casefold() == _EMPTY_MEMBERSHIP_MESSAGE:
            continue
        prefix, separator, detail = message.rpartition(": ")
        if (
            separator
            and _RESOURCE_NAME.fullmatch(prefix) is not None
            and detail.casefold() == _EMPTY_MEMBERSHIP_MESSAGE
        ):
            continue
        return False
    return True


class LsfCommands:
    """Own the execution configuration, runner and captured operating-system user."""

    def __init__(self, config: CollectorConfig, runner: CommandRunner, user: str):
        if config.timeout <= 0:
            raise ValueError("LSF timeout must be positive")
        self.config = config
        self.runner = runner
        self.user = user

    def queues(self) -> tuple[QueueInfo, ...]:
        command = (self.config.bqueues, "-u", self.user, "-w")
        return parse_queues(self._run(command).stdout)

    def _run(self, argv: Sequence[str]) -> CommandResult:
        result = self.runner.run(argv, timeout=self.config.timeout)
        if result.returncode != 0:
            detail = result.stderr.strip().splitlines()
            summary = detail[0][:240] if detail else f"exit status {result.returncode}"
            raise CollectorError(f"{Path(result.argv[0]).name} failed: {summary}")
        if result.stdout.endswith(TRUNCATION_TEXT):
            raise CollectorError(
                f"{Path(result.argv[0]).name} output exceeded "
                f"{STDOUT_LIMIT_BYTES} bytes"
            )
        return result

    def _run_jobs(self, argv: Sequence[str]) -> CommandResult:
        result = self.runner.run(argv, timeout=self.config.timeout)
        if result.returncode == 0:
            if result.stdout.endswith(TRUNCATION_TEXT):
                raise CollectorError(
                    f"{Path(result.argv[0]).name} output exceeded "
                    f"{STDOUT_LIMIT_BYTES} bytes"
                )
            return result
        output_lines = [
            line.strip().casefold()
            for stream in (result.stdout, result.stderr)
            for line in stream.splitlines()
            if line.strip()
        ]
        if output_lines and all(
            line.startswith(("no unfinished job found", "no job found"))
            for line in output_lines
        ):
            return replace(result, returncode=0, stdout="", stderr="")
        detail = result.stderr.strip().splitlines()
        summary = detail[0][:240] if detail else f"exit status {result.returncode}"
        raise CollectorError(f"{Path(result.argv[0]).name} failed: {summary}")

    def hosts(self) -> tuple[HostInfo, ...]:
        command = (self.config.bhosts, "-w")
        return parse_hosts(self._run(command).stdout)

    def host_queues(self, host: str) -> tuple[str, ...]:
        command = (
            self.config.bqueues,
            "-u",
            self.user,
            "-m",
            host,
            "-w",
        )
        result = self.runner.run(command, timeout=self.config.timeout)
        if _is_empty_membership_result(result):
            return ()
        if result.returncode != 0:
            detail = result.stderr.strip().splitlines()
            summary = (
                detail[0][:240]
                if detail
                else f"exit status {result.returncode}"
            )
            raise CollectorError(
                f"{Path(result.argv[0]).name} failed: {summary}"
            )
        if result.stdout.endswith(TRUNCATION_TEXT):
            raise CollectorError(
                f"{Path(result.argv[0]).name} output exceeded "
                f"{STDOUT_LIMIT_BYTES} bytes"
            )
        return parse_queue_names(result.stdout)

    def load(self) -> dict[str, tuple[object, ...]]:
        command = (self.config.lsload, "-I", _LOAD_INDICES)
        return parse_load(self._run(command).stdout)

    def capacities(self) -> dict[str, int | None]:
        """Collect a new capacity sample without the process-memory cache."""
        command = (self.config.lshosts, "-w")
        return parse_host_capacities(self._run(command).stdout)

    def jobs(self) -> tuple[JobInfo, ...]:
        command = (
            self.config.bjobs,
            "-u",
            self.user,
            "-o",
            BJOBS_FORMAT,
            "-noheader",
        )
        return tuple(
            replace(job, user=self.user)
            for job in parse_jobs(self._run_jobs(command).stdout)
        )

    def kill_job(self, job_id: str) -> None:
        if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
            raise CollectorError("Invalid LSF job ID")
        self._run((self.config.bkill, job_id))
