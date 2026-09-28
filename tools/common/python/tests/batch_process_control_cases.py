"""Preserved cancellation-command regressions at the process owner boundary."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any

import pytest

from cadbatch.manifest import load_manifest
from cadbatch.process_control import BatchProcessControl
from batch_controller_fixtures import _python_command, _write_manifest


def test_cancel_command_failure_still_terminates_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _write_manifest(tmp_path, [_python_command("pass")])
    text = manifest_path.read_text(encoding="utf-8").replace(
        'cancel_command = ""', 'cancel_command = "bkill -J test"'
    )
    manifest_path.write_text(text, encoding="utf-8")
    manifest = load_manifest(manifest_path)

    class FakeProcess:
        pid = 12345
        done = False

        def poll(self) -> int | None:
            return 0 if self.done else None

    process = FakeProcess()
    task = manifest.tasks[0]
    process_control = BatchProcessControl()
    process_control.register(task.task_id, process)  # type: ignore[arg-type]
    signals: list[tuple[int, signal.Signals]] = []

    def fail_cancel(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired("bkill", 10)

    def terminate(pid: int, sig: signal.Signals) -> None:
        signals.append((pid, sig))
        process.done = True

    monkeypatch.setattr(subprocess, "run", fail_cancel)
    monkeypatch.setattr(os, "killpg", terminate)

    process_control.terminate_active(
        manifest.tasks, manifest.temp_dir
    )

    assert signals == [(12345, signal.SIGTERM)]


def test_cancel_command_uses_batch_launch_cad_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _write_manifest(tmp_path, [_python_command("pass")])
    text = manifest_path.read_text(encoding="utf-8").replace(
        'cancel_command = ""', 'cancel_command = "bkill -J test"'
    )
    manifest_path.write_text(text, encoding="utf-8")
    manifest = load_manifest(manifest_path)

    class FakeProcess:
        pid = 12345

        def poll(self) -> int | None:
            return None

    task = manifest.tasks[0]
    process_control = BatchProcessControl()
    process_control.register(task.task_id, FakeProcess())  # type: ignore[arg-type]
    captured: dict[str, Any] = {}

    def capture_cancel(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)
        raise subprocess.TimeoutExpired("bkill", 10)

    monkeypatch.setattr(subprocess, "run", capture_cancel)
    monkeypatch.setattr(os, "killpg", lambda *_args: None)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    ticks = iter((0.0, 4.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 4.0))

    process_control.terminate_active(
        manifest.tasks, manifest.temp_dir
    )

    expected = tmp_path / "launch" / ".sico"
    environment = captured["env"]
    assert all(
        environment[name] == str(expected)
        for name in ("SICO_TEMP_DIR", "TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR")
    )
    assert environment["XDG_CACHE_HOME"] == str(expected / "cache")
    assert environment["XDG_RUNTIME_DIR"] == str(expected / "runtime")
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
