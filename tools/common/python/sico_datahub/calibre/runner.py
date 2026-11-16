"""Explicit-lifecycle host for bounded Calibre DRC and LVS jobs."""

from __future__ import annotations

import os
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from ..codec import artifact_ref
from ..errors import HubError
from ..site import safe_site_environment
from .candidate import (
    candidate_artifact,
    normalize_bbox,
    source_artifact,
)
from .collect import collect_run
from .context import resolve_flow
from .execution import prepare_execution
from .export import export_layout as run_export
from .jobs import JobStore
from .process import probe_version, run_process, stop_process
from .receipts import JobReceipt

_MAX_TIMEOUT = 7 * 24 * 60 * 60


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _executable(value, field_name):
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise HubError("capability_not_available", field_name + " is unavailable")
    return path.resolve()


class CalibreRunner:
    """Start one job at a time per run_ref and persist every terminal decision."""

    def __init__(
        self,
        work_root,
        *,
        calibre_root,
        drc_command,
        lvs_command,
        timeout=24 * 60 * 60,
        prepare_timeout=180.0,
        max_active=4,
    ):
        self.store = JobStore(work_root)
        self.work_root = self.store.work_root
        self.calibre_root = Path(calibre_root).expanduser().absolute()
        self.calibre_executable = _executable(
            self.calibre_root / "bin" / "calibre", "Calibre executable"
        )
        self.commands = {
            "drc": _executable(drc_command, "DRC command"),
            "lvs": _executable(lvs_command, "LVS command"),
        }
        if type(timeout) not in {int, float} or not 0 < timeout <= _MAX_TIMEOUT:
            raise HubError("invalid_arguments", "Invalid Calibre timeout")
        if type(max_active) is not int or not 1 <= max_active <= 16:
            raise HubError("invalid_arguments", "Invalid Calibre concurrency limit")
        self.timeout = timeout
        if (
            type(prepare_timeout) not in {int, float}
            or not 0 < prepare_timeout <= 1800
        ):
            raise HubError("invalid_arguments", "Invalid Calibre deck timeout")
        self.prepare_timeout = prepare_timeout
        self.max_active = max_active
        self._version = None
        self._lock = threading.RLock()
        self._active = {}
        self._threads = {}
        self._cancelled = set()
        self._closed = False

    @property
    def tool_version(self):
        with self._lock:
            if self._version is None:
                self._version = probe_version(self.calibre_executable)
            return self._version

    def _environment(self):
        env = safe_site_environment()
        binary = str(self.calibre_root / "bin")
        env["PATH"] = binary + os.pathsep + env.get("PATH", "")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONNOUSERSITE"] = "1"
        return env

    def _reserve(self, run_ref, work_root):
        if not isinstance(run_ref, str):
            raise HubError("invalid_arguments", "Invalid Calibre run_ref")
        if not isinstance(work_root, str) or not work_root:
            raise HubError("invalid_arguments", "Invalid Calibre work root")
        root = Path(work_root).expanduser()
        if not root.is_absolute():
            raise HubError("invalid_arguments", "Calibre work root must be absolute")
        try:
            root = root.resolve()
        except OSError as exc:
            raise HubError("invalid_arguments", "Calibre work root is unavailable") from exc
        if root != self.work_root:
            raise HubError("invalid_arguments", "Calibre work root does not match this runner")
        with self._lock:
            if self._closed:
                raise HubError("capability_not_available", "Calibre runner is closed")
            if len(self._active) >= self.max_active:
                raise HubError("invalid_arguments", "Calibre runner is busy")
            if run_ref in self._active or self.store.exists(run_ref):
                raise HubError("stale_context", "Calibre run_ref already exists")
            self._active[run_ref] = None

    def _abort(self, run_ref):
        with self._lock:
            self._active.pop(run_ref, None)
            self._threads.pop(run_ref, None)

    def _receipt(
        self, kind, run_ref, flow, *, candidate=None, candidate_source=None, scope="full"
    ):
        return JobReceipt(
            run_ref=run_ref,
            kind=kind,
            state="queued",
            config=flow.config,
            work_root=str(self.work_root),
            created_at=_now(),
            runset=flow.runset_identity.runset,
            dependencies_sha256=flow.runset_identity.dependencies_sha256,
            tool_version=self.tool_version,
            top_cell=flow.top_cell,
            candidate=candidate,
            candidate_source=candidate_source,
            scope=scope,
        )

    def _launch(
        self, flow, receipt, candidate_path=None, source_path=None, region=None
    ):
        self.store.create(receipt)
        thread = threading.Thread(
            target=self._execute,
            args=(flow, receipt, candidate_path, source_path, region),
            name="calibre-" + receipt.run_ref,
            daemon=True,
        )
        with self._lock:
            self._threads[receipt.run_ref] = thread
        thread.start()
        return receipt

    def submit(self, kind, run_ref, config_path, work_root):
        self._reserve(run_ref, work_root)
        try:
            flow = resolve_flow(
                kind, config_path, work_root, tool_version=self.tool_version
            )
            return self._launch(flow, self._receipt(kind, run_ref, flow))
        except Exception:
            self._abort(run_ref)
            raise

    def submit_candidate(
        self,
        run_ref,
        config_path,
        work_root,
        candidate_path,
        region=None,
        *,
        kind="drc",
        source_path=None,
    ):
        if kind not in {"drc", "lvs"}:
            raise HubError("invalid_arguments", "Unsupported candidate check kind")
        candidate = candidate_artifact(candidate_path)
        candidate_source = source_artifact(source_path) if source_path else None
        if kind == "lvs" and candidate_source is None:
            raise HubError("invalid_arguments", "LVS candidate requires a source netlist")
        normalized_region = normalize_bbox(region)
        self._reserve(run_ref, work_root)
        try:
            flow = resolve_flow(
                kind, config_path, work_root, tool_version=self.tool_version
            )
            receipt = self._receipt(
                kind,
                run_ref,
                flow,
                candidate=candidate,
                candidate_source=candidate_source,
                scope="candidate",
            )
            return self._launch(
                flow,
                receipt,
                candidate_path=candidate.path,
                source_path=candidate_source.path if candidate_source else None,
                region=normalized_region,
            )
        except Exception:
            self._abort(run_ref)
            raise

    def export_layout(self, export_ref, config_path, work_root):
        self._reserve(export_ref, work_root)
        try:
            flow = resolve_flow(
                "lvs", config_path, work_root, tool_version=self.tool_version
            )
            return run_export(
                flow,
                store=self.store,
                export_ref=export_ref,
                lvs_command=self.commands["lvs"],
                environment=self._environment(),
                tool_version=self.tool_version,
                timeout=self.prepare_timeout,
            )
        finally:
            self._abort(export_ref)

    def _execute(
        self, flow, receipt, candidate_path=None, source_path=None, region=None
    ):
        run_ref = receipt.run_ref
        stdout_path = self.store.log_path(run_ref, "out")
        stderr_path = self.store.log_path(run_ref, "err")
        current = replace(receipt, state="running")
        self.store.save(current)
        try:
            plan = prepare_execution(
                flow,
                store=self.store,
                run_ref=run_ref,
                candidate_path=candidate_path,
                source_path=source_path,
                region=region,
                calibre_executable=self.calibre_executable,
                commands=self.commands,
                work_root=self.work_root,
                timeout=self.prepare_timeout,
            )
            current = replace(
                current,
                effective_deck=plan.prepared.effective_deck,
                effective_config=plan.prepared.config,
                candidate=plan.candidate or current.candidate,
                candidate_source=plan.source or current.candidate_source,
                scope=plan.scope,
            )
            self.store.save(current)
            with self._lock:
                if run_ref in self._cancelled:
                    self.store.save(replace(
                        current,
                        state="cancelled",
                        finished_at=_now(),
                        message="Calibre job cancelled before start",
                    ))
                    return

            def started(process):
                with self._lock:
                    self._active[run_ref] = process

            outcome = run_process(
                plan.command,
                cwd=plan.cwd,
                env=self._environment(),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                timeout=self.timeout,
                on_start=started,
            )
            with self._lock:
                cancelled = run_ref in self._cancelled
            if cancelled:
                final = replace(
                    current,
                    state="cancelled",
                    started_at=outcome.started_at,
                    finished_at=outcome.finished_at,
                    pid=outcome.pid,
                    exit_code=outcome.exit_code,
                    message="Calibre job cancelled",
                    stdout=artifact_ref(stdout_path),
                    stderr=artifact_ref(stderr_path),
                )
                self.store.save(final)
                return
            collected = collect_run(
                plan.flow,
                store=self.store,
                run_ref=run_ref,
                exit_code=outcome.exit_code,
                timed_out=outcome.timed_out,
                tool_version=self.tool_version,
                started_at=outcome.started_at,
                finished_at=outcome.finished_at,
                prepared=plan.prepared,
                candidate_source=plan.source,
            )
            snapshot = None
            if collected.result is not None:
                self.store.write_result(run_ref, collected.result)
                snapshot = artifact_ref(self.store.result_path(run_ref))
            artifacts = collected.artifacts
            final = replace(
                current,
                state=collected.state,
                started_at=outcome.started_at,
                finished_at=outcome.finished_at,
                pid=outcome.pid,
                exit_code=outcome.exit_code,
                message=collected.message,
                effective_deck=plan.prepared.effective_deck,
                effective_config=plan.prepared.config,
                candidate=plan.candidate or current.candidate,
                candidate_source=plan.source or current.candidate_source,
                stdout=artifact_ref(stdout_path),
                stderr=artifact_ref(stderr_path),
                results=artifacts.get("results", current.results),
                summary=artifacts.get("summary", current.summary),
                report=artifacts.get("report", current.report),
                svdb=artifacts.get("svdb", current.svdb),
                lvs_log=artifacts.get("log", current.lvs_log),
                result_snapshot=snapshot,
            )
            self.store.save(final)
        except HubError as exc:
            self._fail(run_ref, current, stdout_path, stderr_path, exc.message)
        except Exception:
            self._fail(
                run_ref,
                current,
                stdout_path,
                stderr_path,
                "Calibre result collection failed",
            )
        finally:
            with self._lock:
                self._active.pop(run_ref, None)
                self._threads.pop(run_ref, None)
                self._cancelled.discard(run_ref)

    def cancel(self, run_ref):
        """Stop one active job and make its terminal state explicit."""
        with self._lock:
            receipt = self.store.load(run_ref)
            if receipt.terminal:
                return receipt
            process = self._active.get(run_ref)
            thread = self._threads.get(run_ref)
            self._cancelled.add(run_ref)
            if process is None and thread is None:
                receipt = replace(
                    receipt,
                    state="cancelled",
                    finished_at=_now(),
                    message="Calibre job cancelled before start",
                )
                self.store.save(receipt)
                return receipt
        if process is not None:
            stop_process(process)
        if thread is not None:
            thread.join(timeout=10)
        return self.status(run_ref)

    def _fail(self, run_ref, current, stdout_path, stderr_path, message):
        try:
            stdout = artifact_ref(stdout_path) if stdout_path.is_file() else None
            stderr = artifact_ref(stderr_path) if stderr_path.is_file() else None
            self.store.save(replace(
                current,
                state="failed",
                finished_at=_now(),
                message=message,
                stdout=stdout,
                stderr=stderr,
            ))
        except Exception:
            pass

    def status(self, run_ref):
        receipt = self.store.load(run_ref)
        with self._lock:
            active = run_ref in self._active
        if not receipt.terminal and not active:
            latest = self.store.load(run_ref)
            if latest.terminal:
                return latest
            receipt = replace(
                latest,
                state="unknown",
                finished_at=latest.finished_at or _now(),
                message="Calibre job state is unknown after runner restart",
            )
            self.store.save(receipt)
        return receipt

    def result(self, run_ref):
        receipt = self.status(run_ref)
        if not receipt.terminal:
            raise HubError("invalid_arguments", "Calibre job has not completed")
        if receipt.result_snapshot is None:
            raise HubError(
                "source_not_captured", "Calibre result snapshot is unavailable"
            )
        return receipt, self.store.read_result(run_ref)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            processes = [process for process in self._active.values() if process]
            threads = list(self._threads.values())
        for process in processes:
            stop_process(process)
        for thread in threads:
            thread.join(timeout=5)


__all__ = ["CalibreRunner"]
