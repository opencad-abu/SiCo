from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from cadlsf import CollectorCancelled
from cadlsf.collector import CollectorError, STDERR_LIMIT_BYTES, SubprocessRunner


from cadlsf_fixtures import CAD_ROOT


def test_subprocess_runner_uses_argv_fixed_locale_and_timeout(tmp_path: Path) -> None:
    script = tmp_path / "probe"
    script.write_text(
        "#!/bin/sh\nprintf '%s|%s\\n' \"$LC_ALL\" \"$1\"\n",
        encoding="ascii",
    )
    script.chmod(0o755)

    result = SubprocessRunner({"PATH": "/bin"}).run(
        (str(script), "literal;not-shell"), timeout=2.0
    )

    assert result.returncode == 0
    assert result.stdout == "C|literal;not-shell\n"
    with pytest.raises(CollectorError, match="unavailable"):
        SubprocessRunner().run((str(tmp_path / "missing"),), timeout=1.0)


def test_subprocess_runner_wraps_tempfile_and_spawn_os_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_tempfile(*_args: object, **_kwargs: object) -> object:
        raise PermissionError("temporary storage denied")

    monkeypatch.setattr("cadlsf.command_runner.tempfile.TemporaryFile", deny_tempfile)
    with pytest.raises(
        CollectorError, match="Cannot buffer.*temporary storage denied"
    ):
        SubprocessRunner().run((sys.executable, "--version"), timeout=1.0)

    monkeypatch.undo()

    def deny_spawn(*_args: object, **_kwargs: object) -> object:
        raise PermissionError("process creation denied")

    monkeypatch.setattr("cadlsf.command_runner.subprocess.Popen", deny_spawn)
    with pytest.raises(
        CollectorError, match="Cannot start.*process creation denied"
    ):
        SubprocessRunner().run((sys.executable, "--version"), timeout=1.0)


def test_subprocess_runner_enforces_timeout_and_bounds_stderr() -> None:
    with pytest.raises(CollectorError, match="timed out"):
        SubprocessRunner().run(
            (sys.executable, "-c", "import time; time.sleep(2)"), timeout=0.05
        )

    result = SubprocessRunner().run(
        (
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('x' * 100000); raise SystemExit(1)",
        ),
        timeout=2.0,
    )

    assert len(result.stderr.encode("utf-8")) <= STDERR_LIMIT_BYTES
    assert result.stderr.endswith("[output truncated]\n")


def test_subprocess_runner_cooperatively_cancels_active_command() -> None:
    cancelled = False

    def should_cancel() -> bool:
        nonlocal cancelled
        if cancelled:
            return True
        cancelled = True
        return False

    with pytest.raises(CollectorCancelled, match="cancelled"):
        SubprocessRunner(cancelled=should_cancel).run(
            (sys.executable, "-c", "import time; time.sleep(10)"), timeout=20
        )


def test_subprocess_runner_cancellation_signals_entire_process_group(
    tmp_path: Path,
) -> None:
    child_ready = tmp_path / "child-ready"
    child_terminated = tmp_path / "child-terminated"
    script = tmp_path / "process-tree.py"
    script.write_text(
        """import signal
import subprocess
import sys
import time

child_code = '''import os
import signal
import sys
import time

ready, terminated = sys.argv[1:]
def stop(_signum, _frame):
    with open(terminated, "w", encoding="ascii") as stream:
        stream.write(str(os.getpid()))
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
with open(ready, "w", encoding="ascii") as stream:
    stream.write(str(os.getpid()))
while True:
    time.sleep(1)
'''
subprocess.Popen([sys.executable, "-c", child_code, sys.argv[1], sys.argv[2]])
while True:
    time.sleep(1)
""",
        encoding="ascii",
    )

    with pytest.raises(CollectorCancelled, match="cancelled"):
        SubprocessRunner(cancelled=child_ready.exists).run(
            (
                sys.executable,
                str(script),
                str(child_ready),
                str(child_terminated),
            ),
            timeout=10.0,
        )

    assert child_ready.is_file()
    assert child_terminated.is_file()


def test_cli_sigterm_cancels_active_lsf_process_group(tmp_path: Path) -> None:
    ready = tmp_path / "bqueues-ready"
    terminated = tmp_path / "bqueues-terminated"
    bqueues = tmp_path / "bqueues"
    bqueues.write_text(
        """#!/usr/bin/env python3
import os
import signal
import sys
import time

ready, terminated = os.environ["CAD_LSF_TEST_FILES"].split(":", 1)
def stop(_signum, _frame):
    with open(terminated, "w", encoding="ascii") as stream:
        stream.write(str(os.getpid()))
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
with open(ready, "w", encoding="ascii") as stream:
    stream.write(str(os.getpid()))
while True:
    time.sleep(1)
""",
        encoding="ascii",
    )
    bqueues.chmod(0o755)
    output = tmp_path / "queues.tsv"
    environment = os.environ.copy()
    environment["CAD_LSF_TEST_FILES"] = f"{ready}:{terminated}"
    process = subprocess.Popen(
        [
            str(CAD_ROOT / "common/python/sico-lsf"),
            "--bqueues",
            str(bqueues),
            "queues",
            "--format",
            "tsv",
            "--output",
            str(output),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    try:
        deadline = time.monotonic() + 5.0
        while not ready.is_file() and process.poll() is None:
            if time.monotonic() >= deadline:
                pytest.fail("fake bqueues did not start")
            time.sleep(0.02)
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5.0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2.0)

    assert process.returncode == 128 + signal.SIGTERM, (stdout, stderr)
    assert terminated.is_file()
    assert not output.exists()
