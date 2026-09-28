"""Own running batch processes and their process-group cancellation."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterable

from .environment import task_environment
from .manifest import TaskSpec


class BatchProcessControl:
    """Track task processes and terminate them as one cancellation unit."""

    def __init__(self) -> None:
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._cancel_commands_run: set[str] = set()
        self._lock = threading.RLock()

    def register(self, task_id: str, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._processes[task_id] = process

    def remove(self, task_id: str) -> None:
        with self._lock:
            self._processes.pop(task_id, None)

    def _active(
        self, tasks: Iterable[TaskSpec]
    ) -> list[tuple[TaskSpec, subprocess.Popen[bytes]]]:
        with self._lock:
            return [
                (task, self._processes[task.task_id])
                for task in tasks
                if task.task_id in self._processes
            ]

    def terminate_active(self, tasks: Iterable[TaskSpec], temp_dir: Path) -> None:
        """Run each cancellation command once, then stop process groups."""

        active = self._active(tasks)
        for task, process in active:
            if process.poll() is not None:
                continue
            if task.cancel_command and self._mark_cancel_command(task.task_id):
                try:
                    subprocess.run(
                        ["/bin/sh", "-c", task.cancel_command],
                        cwd=str(task.run_dir),
                        env=task_environment(temp_dir),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                        check=False,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    print(
                        f"[BATCH][WARN] {task.label}: cancel command failed: {exc}",
                        flush=True,
                    )
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if all(process.poll() is not None for _, process in active):
                return
            time.sleep(0.05)
        for _, process in active:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def _mark_cancel_command(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self._cancel_commands_run:
                return False
            self._cancel_commands_run.add(task_id)
            return True


__all__ = ["BatchProcessControl"]
