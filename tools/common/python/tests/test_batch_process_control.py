"""Cancellation behavior without exposing the process owner's registry."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from itertools import count
import signal
import subprocess

import pytest

from cadbatch import process_control as owner
from cadbatch.manifest import load_manifest
from cadbatch.process_control import BatchProcessControl
from batch_controller_fixtures import _python_command, _write_manifest


class Process:
    def __init__(self, pid=12345, done=False):
        self.pid = pid
        self.done = done

    def poll(self):
        return 0 if self.done else None


@pytest.fixture
def cancellation(tmp_path, monkeypatch):
    manifest = load_manifest(_write_manifest(tmp_path, [_python_command("pass")]))
    task = replace(manifest.tasks[0], cancel_command="fixture-cancel")
    ticks = count(0.0, 4.0)
    monkeypatch.setattr(owner.time, "monotonic", lambda: next(ticks))
    return BatchProcessControl(), task, manifest.temp_dir


@pytest.mark.parametrize("failure", [None, OSError("unavailable"),
                                     subprocess.TimeoutExpired("fixture-cancel", 10)])
def test_cancel_command_runs_once_and_stubborn_group_is_killed(
    cancellation, monkeypatch, failure
):
    control, task, temp_dir = cancellation
    process = Process()
    control.register(task.task_id, process)
    commands, signals = [], []

    def cancel(argv, **kwargs):
        commands.append((argv, kwargs))
        if failure is not None:
            raise failure

    monkeypatch.setattr(owner.subprocess, "run", cancel)
    monkeypatch.setattr(owner.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    control.terminate_active((task,), temp_dir)
    control.terminate_active((task,), temp_dir)

    assert len(commands) == 1
    assert commands[0][0] == ["/bin/sh", "-c", "fixture-cancel"]
    assert commands[0][1]["timeout"] == 10
    assert commands[0][1]["cwd"] == str(task.run_dir)
    assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)] * 2


@pytest.mark.parametrize("state", ["removed", "exited", "unregistered"])
def test_finished_or_unregistered_process_is_not_cancelled(
    cancellation, monkeypatch, state
):
    control, task, temp_dir = cancellation
    if state != "unregistered":
        control.register(task.task_id, Process(done=state == "exited"))
    if state == "removed":
        control.remove(task.task_id)
        control.remove(task.task_id)

    def unexpected(*args, **kwargs):
        pytest.fail("inactive process must not receive a cancel command or signal")

    monkeypatch.setattr(owner.subprocess, "run", unexpected)
    monkeypatch.setattr(owner.os, "killpg", unexpected)
    control.terminate_active((task,), temp_dir)


@pytest.mark.parametrize("vanishes_at", [signal.SIGTERM, signal.SIGKILL])
def test_process_group_disappearing_during_cancel_is_harmless(
    cancellation, monkeypatch, vanishes_at
):
    control, task, temp_dir = cancellation
    task = replace(task, cancel_command="")
    process = Process()
    signals = []
    control.register(task.task_id, process)

    def kill(pid, sig):
        signals.append((pid, sig))
        if sig == vanishes_at:
            process.done = True
            raise ProcessLookupError(pid)

    monkeypatch.setattr(owner.os, "killpg", kill)
    control.terminate_active((task,), temp_dir)
    expected = [signal.SIGTERM]
    if vanishes_at == signal.SIGKILL:
        expected.append(signal.SIGKILL)
    assert signals == [(process.pid, sig) for sig in expected]


def test_cancel_command_does_not_block_process_registration(cancellation, monkeypatch):
    control, task, temp_dir = cancellation
    first, second = Process(), Process(pid=23456)
    later = replace(task, task_id="002", cancel_command="")
    control.register(task.task_id, first)
    signals, registered = [], []
    with ThreadPoolExecutor(max_workers=1) as executor:
        def cancel(*args, **kwargs):
            # Registration happens on a worker while cancellation runs on the
            # caller thread. Holding the registry lock across run() deadlocks.
            future = executor.submit(control.register, later.task_id, second)
            future.result(timeout=1)
            registered.append(True)

        def kill(pid, sig):
            signals.append((pid, sig))
            (first if pid == first.pid else second).done = True

        monkeypatch.setattr(owner.subprocess, "run", cancel)
        monkeypatch.setattr(owner.os, "killpg", kill)
        control.terminate_active((task, later), temp_dir)
        assert registered == [True]
        assert signals == [(first.pid, signal.SIGTERM)]
        control.terminate_active((task, later), temp_dir)
    assert signals == [(first.pid, signal.SIGTERM), (second.pid, signal.SIGTERM)]
