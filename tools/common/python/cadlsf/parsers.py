"""Pure parsers for supported LSF command text and numeric units."""

from __future__ import annotations

import re
from typing import Mapping, Sequence

from .command_runner import CollectorError
from .model import HostInfo, JobInfo, QueueInfo

_INTEGER = re.compile(r"^[0-9]+$")
_MEMORY = re.compile(r"^([0-9]+(?:\.[0-9]+)?)([KMGTPE]?)(?:I?B)?$", re.IGNORECASE)
_MEMORY_FACTORS = {
    "": 1,
    "K": 1024,
    "M": 1024**2,
    "G": 1024**3,
    "T": 1024**4,
    "P": 1024**5,
    "E": 1024**6,
}
BJOBS_DELIMITER = "\x1f"
BJOBS_FIELDS = (
    "jobid",
    "stat",
    "queue",
    "first_host",
    "exec_host",
    "slots",
    "submit_time",
    "start_time",
    "run_time",
    "job_name",
)
BJOBS_FORMAT = " ".join(BJOBS_FIELDS) + (
    f' delimiter="{BJOBS_DELIMITER}"'
)


def _integer(token: str) -> int | None:
    return int(token) if _INTEGER.fullmatch(token) else None


def _float(token: str) -> float | None:
    normalized = token.lstrip("*")
    if normalized in {"", "-", "N/A", "n/a"}:
        return None
    try:
        value = float(normalized.rstrip("%"))
    except ValueError:
        return None
    return value / 100 if normalized.endswith("%") else value


def _bytes(token: str) -> int | None:
    match = _MEMORY.fullmatch(token.strip())
    if match is None:
        return None
    value, suffix = match.groups()
    return int(float(value) * _MEMORY_FACTORS[suffix.upper()])


def _capacity_bytes(token: str) -> int | None:
    value = _bytes(token)
    if value is None:
        return None
    match = _MEMORY.fullmatch(token.strip())
    return value * 1024 if match is not None and not match.group(2) else value


def _table_rows(
    output: str, required_columns: Sequence[str], command: str
) -> tuple[dict[str, int], list[list[str]]]:
    lines = [line.split() for line in output.splitlines() if line.strip()]
    if not lines:
        raise CollectorError(f"{command} returned malformed output")
    header = {name.upper(): index for index, name in enumerate(lines[0])}
    if any(name not in header for name in required_columns):
        raise CollectorError(f"{command} returned malformed output")
    return header, lines[1:]


def _column(row: list[str], header: Mapping[str, int], name: str) -> str:
    index = header[name]
    return row[index] if index < len(row) else ""


def parse_queues(output: str) -> tuple[QueueInfo, ...]:
    header, rows = _table_rows(
        output,
        ("QUEUE_NAME", "STATUS", "NJOBS", "PEND", "RUN"),
        "bqueues",
    )
    queues: list[QueueInfo] = []
    seen: set[str] = set()
    for row in rows:
        name = _column(row, header, "QUEUE_NAME")
        status = _column(row, header, "STATUS")
        total = _integer(_column(row, header, "NJOBS"))
        pending = _integer(_column(row, header, "PEND"))
        running = _integer(_column(row, header, "RUN"))
        is_open = status.casefold().startswith("open")
        if name and name not in seen:
            queues.append(QueueInfo(name, status, is_open, total, pending, running))
            seen.add(name)
    return tuple(queues)


def parse_hosts(output: str) -> tuple[HostInfo, ...]:
    columns = ("HOST_NAME", "STATUS", "MAX", "NJOBS", "RUN", "SSUSP", "USUSP", "RSV")
    header, rows = _table_rows(output, columns, "bhosts")
    hosts: list[HostInfo] = []
    seen: set[str] = set()
    for row in rows:
        name = _column(row, header, "HOST_NAME")
        status = _column(row, header, "STATUS")
        metrics = tuple(_integer(_column(row, header, column)) for column in columns[2:])
        if name and name not in seen:
            is_available = status.casefold() == "ok"
            unavailable = [
                metric_name
                for metric_name, value in zip(
                    ("max_slots", "total_jobs", "running_jobs"), metrics[:3]
                )
                if value is None
            ]
            suspended = None
            if metrics[3] is not None and metrics[4] is not None:
                suspended = metrics[3] + metrics[4]
            else:
                unavailable.append("suspended_jobs")
            if metrics[5] is None:
                unavailable.append("reserved_slots")
            hosts.append(
                HostInfo(
                    name=name,
                    status=status,
                    is_available=is_available,
                    max_slots=metrics[0],
                    total_jobs=metrics[1],
                    running_jobs=metrics[2],
                    suspended_jobs=suspended,
                    reserved_slots=metrics[5],
                    unavailable_metrics=tuple(unavailable),
                )
            )
            seen.add(name)
    return tuple(hosts)


def parse_load(output: str) -> dict[str, tuple[object, ...]]:
    columns = ("HOST_NAME", "R1M", "UT", "MEM", "SWP")
    header, rows = _table_rows(output, columns, "lsload")
    result: dict[str, tuple[object, ...]] = {}
    for row in rows:
        name = _column(row, header, "HOST_NAME")
        tokens = [_column(row, header, column) for column in columns[1:]]
        if name:
            result[name] = (_float(tokens[0]), _float(tokens[1]), _bytes(tokens[2]), _bytes(tokens[3]))
    return result


def parse_host_capacities(
    output: str,
) -> dict[str, int | None]:
    columns = ("HOST_NAME", "MAXMEM")
    header, rows = _table_rows(output, columns, "lshosts")
    capacities: dict[str, int | None] = {}
    for row in rows:
        name = _column(row, header, "HOST_NAME")
        if name and name not in capacities:
            capacities[name] = _capacity_bytes(
                _column(row, header, "MAXMEM")
            )
    return capacities


def parse_queue_names(output: str) -> tuple[str, ...]:
    header, rows = _table_rows(output, ("QUEUE_NAME",), "bqueues")
    return tuple(
        name
        for row in rows
        if (name := _column(row, header, "QUEUE_NAME"))
    )


def parse_jobs(output: str) -> tuple[JobInfo, ...]:
    lines = [
        line.rstrip("\r")
        for line in output.split("\n")
        if line.rstrip("\r")
    ]
    if not lines:
        return ()
    if all(
        line.casefold().startswith(("no unfinished job found", "no job found"))
        for line in lines
    ):
        return ()
    jobs: list[JobInfo] = []
    for line in lines:
        columns = [
            value.strip()
            for value in line.split(BJOBS_DELIMITER)
        ]
        if len(columns) != len(BJOBS_FIELDS):
            raise CollectorError("bjobs returned malformed output")
        (
            job_id,
            status,
            queue,
            first_execution_host,
            execution_hosts,
            slots,
            submit_time,
            start_time,
            run_time,
            name,
        ) = columns
        if not job_id or not status:
            raise CollectorError("bjobs returned malformed output")
        run_time_parts = run_time.split(maxsplit=1)
        jobs.append(
            JobInfo(
                job_id=job_id,
                user="",
                status=status,
                queue=queue,
                first_execution_host=(
                    "" if first_execution_host == "-" else first_execution_host
                ),
                execution_hosts=(
                    "" if execution_hosts == "-" else execution_hosts
                ),
                slots=_integer(slots),
                submit_time=submit_time,
                start_time=start_time,
                run_time_seconds=(
                    _integer(run_time_parts[0]) if run_time_parts else None
                ),
                name=name,
            )
        )
    return tuple(jobs)
