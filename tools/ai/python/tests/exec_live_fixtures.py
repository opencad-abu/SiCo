"""Opt-in fixed-binary managed execution, isolated from real model credentials."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import time

import pytest

from sico.codex.exec_process import ManagedExecProcess


@pytest.fixture
def managed_configuration(tmp_path):
    if os.environ.get("SICO_RUN_EXEC_SERVER_TESTS") != "1":
        pytest.skip("Requires explicitly enabled managed exec-server qualification")
    binary = Path(os.environ["SICO_TEST_CODEX_BINARY"])
    lock = Path(__file__).resolve().parents[2] / "terminal/codex-runtime.lock"
    pins = dict(line.split("=", 1) for line in lock.read_text().splitlines() if "=" in line)
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == pins["binary_sha256"]
    home, work = tmp_path / "private-home", tmp_path / "work space"
    home.mkdir(mode=0o700)
    work.mkdir(mode=0o700)
    (work / "next").mkdir()
    control_home = tmp_path / "execution-control"
    control_home.mkdir(mode=0o700)
    control = dict(PATH="/usr/bin:/bin", HOME=str(home), CODEX_HOME=str(control_home),
                   SICO_API_KEY="synthetic-control-only")
    child = dict(PATH="/usr/bin:/bin", HOME=str(home), LANG="C.UTF-8", EMPTY="")

    return binary, control, child, work, home


@pytest.fixture
def managed_process(managed_configuration):
    binary, control, child, work, home = managed_configuration

    @contextmanager
    def start(argv=None, *, access="read-only", environment=None, network=False):
        process = ManagedExecProcess(binary, control, cwd=work, home=home,
            access=dict(sandbox=access, approval="never", network=network))
        try:
            process.start(argv or ["/bin/bash", "--noprofile", "--norc"],
                          child if environment is None else environment)
            yield process, work
        finally:
            process.close()
    return start


def until(process, predicate, timeout=5):
    events = []
    deadline = time.monotonic() + timeout
    while not predicate(events):
        if time.monotonic() >= deadline:
            pytest.fail("Managed process observations timed out: " + repr(events[-3:]))
        event = process.read(timeout=.2)
        if event is not None:
            events.append(event)
    return events


def drained(process, timeout=5):
    return until(process, lambda rows: any(row.kind == "closed" for row in rows), timeout)


def output(events, stream="stdout"):
    return b"".join(row.data for row in events if row.kind == "output" and row.stream == stream)
