"""Bounded parallel execution for independent DRC, LVS, and RCE tasks."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
import signal
import subprocess
import threading
import time
from typing import Any

from .environment import task_environment
from .manifest import BatchManifest, TaskSpec, load_manifest
from .process_control import BatchProcessControl
from .status import (
    TERMINAL_STATES,
    counts,
    finalize_publications,
    overall_status,
    timestamp,
    write_status,
)

__all__ = [
    "BatchController",
    "BatchManifest",
    "finalize_publications",
    "load_manifest",
]


class BatchController:
    def __init__(self, manifest: BatchManifest) -> None:
        self.manifest = manifest
        self._lock = threading.RLock()
        self._cancel_requested = threading.Event()
        self._process_control = BatchProcessControl()
        self._started_monotonic: dict[str, float] = {}
        self._batch_started = timestamp()
        self._batch_finished = ""
        self._records = [self._initial_record(task) for task in manifest.tasks]
        self._records_by_id = {
            record["id"]: record for record in self._records
        }
        self._old_handlers: dict[int, Any] = {}

    @staticmethod
    def _initial_record(task: TaskSpec) -> dict[str, Any]:
        return {
            "index": task.index,
            "id": task.task_id,
            "label": task.label,
            "status": "pending",
            "exit_code": None,
            "pid": None,
            "run_dir": str(task.run_dir),
            "config": str(task.config),
            "launch_log": str(task.launch_log),
            "started_at": "",
            "finished_at": "",
            "duration_seconds": None,
            "result_path": task.result_path,
            "publication_status": "pending" if task.publication_required else "-",
            "publication_message": "",
            "warning_message": "",
        }

    def run(self) -> int:
        self._install_signal_handlers()
        self._persist()
        print(
            f"[BATCH] Starting {self.manifest.flow} batch "
            f"{self.manifest.batch_id}: {len(self.manifest.tasks)} tasks, "
            f"parallel_cells={self.manifest.parallel_cells}",
            flush=True,
        )
        pending = list(self.manifest.tasks)
        active: dict[Future[None], TaskSpec] = {}
        executor = ThreadPoolExecutor(
            max_workers=self.manifest.parallel_cells,
            thread_name_prefix="sico-batch",
        )
        try:
            while pending or active:
                if self._cancel_requested.is_set():
                    self._cancel_pending(pending)
                    pending.clear()
                    self._terminate_active()
                while (
                    pending
                    and not self._cancel_requested.is_set()
                    and len(active) < self.manifest.parallel_cells
                ):
                    task = pending.pop(0)
                    active[executor.submit(self._run_task, task)] = task
                if not active:
                    continue
                done, _ = wait(tuple(active), timeout=0.2)
                for future in done:
                    task = active.pop(future)
                    try:
                        future.result()
                    except Exception as exc:  # defensive controller boundary
                        self._finish_task(task, "failed", 1)
                        print(
                            f"[BATCH][ERROR] {task.label}: controller error: {exc}",
                            flush=True,
                        )
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            self._restore_signal_handlers()
        self._batch_finished = timestamp()
        self._persist()
        counts = self._counts()
        print(
            f"[BATCH] Finished {self.manifest.batch_id}: "
            f"succeeded={counts['succeeded']} failed={counts['failed']} "
            f"warnings={counts['succeeded_with_warnings']} "
            f"canceled={counts['canceled']}",
            flush=True,
        )
        if counts["canceled"]:
            return 130
        return 1 if counts["failed"] else 0

    def _run_task(self, task: TaskSpec) -> None:
        if self._cancel_requested.is_set():
            self._finish_task(task, "canceled", 130)
            return
        task.launch_log.parent.mkdir(parents=True, exist_ok=True)
        task.run_dir.mkdir(parents=True, exist_ok=True)
        environment = task_environment(self.manifest.temp_dir)
        started = time.monotonic()
        with task.launch_log.open("w", encoding="utf-8", errors="replace") as log:
            process = subprocess.Popen(
                ["/bin/sh", "-c", task.command],
                cwd=str(task.run_dir),
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            with self._lock:
                self._process_control.register(task.task_id, process)
                self._started_monotonic[task.task_id] = started
                record = self._records_by_id[task.task_id]
                record.update(
                    status="running",
                    pid=process.pid,
                    started_at=timestamp(),
                )
                self._persist_locked()
            print(f"[BATCH] Running {task.task_id} {task.label}", flush=True)
            exit_code = process.wait()
        with self._lock:
            self._process_control.remove(task.task_id)
        warning = ""
        if self.manifest.flow == "RCE" and exit_code == 0:
            marker = task.run_dir / "log/lvs-ignored-mismatch"
            if marker.is_file() and marker.stat().st_size:
                warning = "LVS INCORRECT was ignored; output is for non-signoff debug only."
        with self._lock:
            self._records_by_id[task.task_id]["warning_message"] = warning
        if self._cancel_requested.is_set():
            status = "canceled"
        elif exit_code != 0:
            status = "failed"
        elif task.publication_required:
            status = "awaiting_publication"
        elif warning:
            status = "succeeded_with_warnings"
        else:
            status = "succeeded"
        self._finish_task(task, status, exit_code)
        print(
            f"[BATCH] {status.capitalize()} {task.task_id} {task.label} "
            f"exit={exit_code}",
            flush=True,
        )

    def _finish_task(self, task: TaskSpec, status: str, exit_code: int) -> None:
        with self._lock:
            record = self._records_by_id[task.task_id]
            if record["status"] in TERMINAL_STATES:
                return
            started = self._started_monotonic.get(task.task_id)
            record.update(
                status=status,
                exit_code=exit_code,
                pid=None,
                finished_at=timestamp(),
                duration_seconds=(
                    round(time.monotonic() - started, 3) if started is not None else 0.0
                ),
            )
            self._persist_locked()

    def _cancel_pending(self, pending: list[TaskSpec]) -> None:
        for task in pending:
            self._finish_task(task, "canceled", 130)

    def _terminate_active(self) -> None:
        self._process_control.terminate_active(
            self.manifest.tasks, self.manifest.temp_dir
        )

    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        for signum in (signal.SIGINT, signal.SIGTERM):
            self._old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self._request_cancel)

    def _restore_signal_handlers(self) -> None:
        for signum, handler in self._old_handlers.items():
            signal.signal(signum, handler)
        self._old_handlers.clear()

    def _request_cancel(self, signum: int, _frame: Any) -> None:
        if not self._cancel_requested.is_set():
            print(f"[BATCH] Cancellation requested by signal {signum}", flush=True)
        self._cancel_requested.set()

    def _counts(self) -> dict[str, int]:
        with self._lock:
            return counts(self._records)

    def _overall_status(self) -> str:
        return overall_status(
            self._records, cancel_requested=self._cancel_requested.is_set()
        )

    def _persist(self) -> None:
        with self._lock:
            self._persist_locked()

    def _persist_locked(self) -> None:
        counts = self._counts()
        payload = {
            "schema_version": self.manifest.schema_version,
            "batch_id": self.manifest.batch_id,
            "flow": self.manifest.flow,
            "status": self._overall_status(),
            "parallel_cells": self.manifest.parallel_cells,
            "temp_dir": str(self.manifest.temp_dir),
            "manifest": str(self.manifest.path),
            "started_at": self._batch_started,
            "finished_at": self._batch_finished,
            "counts": counts,
            "tasks": self._records,
        }
        write_status(self.manifest, payload)
