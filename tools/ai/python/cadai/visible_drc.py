"""Compose visible-window OASIS export with a DataHub candidate DRC check."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .visible_drc_cancel_watch import start_cancel_watch
from .visible_drc_event_view import (
    cancel_requested,
    clear_cancel,
    discard_job,
    remember_state,
    with_events,
)
from .visible_drc_job import Job
from .visible_drc_list import service_list_jobs
from .visible_drc_payload import payload as response_payload
from .visible_drc_recheck import (
    config_identity,
    delta,
    discard_export,
    reuse_basis,
)
from .visible_drc_region import region as export_region
from .visible_drc_region import skill_region
from .visible_drc_registry import VisibleDrcRegistry, record_from_values
from .visible_drc_restore import RestoreError, restore_job

NAME = "check_visible_layout_drc"
START_NAME = "start_visible_layout_drc"
GET_NAME = "get_visible_layout_drc"
CANCEL_NAME = "cancel_visible_layout_drc"
LIST_NAME = "list_visible_layout_drc"
NAMES = frozenset({NAME, START_NAME, GET_NAME, CANCEL_NAME, LIST_NAME})
MAX_TIMEOUT = 1800.0
_WINDOW = re.compile(r"^wctx_[A-Za-z0-9_-]+_[0-9]{1,10}$")
_JOB = re.compile(r"^visible-[a-f0-9]{32}$")


class VisibleDrcArgumentError(ValueError):
    """Raised when the visible DRC request violates its bounded contract."""


class VisibleDrcError(RuntimeError):
    """A stable runtime failure returned through the MCP result envelope."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _text(value: Any, field: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise VisibleDrcArgumentError(f"{field} must be a non-empty trimmed string")
    if len(value) > maximum:
        raise VisibleDrcArgumentError(f"{field} exceeds its length limit")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise VisibleDrcArgumentError(f"{field} contains a control character")
    return value


def _absolute_file(value: Any, field: str) -> Path:
    path = Path(_text(value, field)).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise VisibleDrcArgumentError(f"{field} must be an absolute file")
    return path.resolve()


def _absolute_dir(value: Any, field: str) -> Path:
    path = Path(_text(value, field)).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise VisibleDrcArgumentError(f"{field} must be an absolute directory")
    return path.resolve()


def _timeout(value: Any) -> float:
    if type(value) not in {int, float} or not 0 < float(value) <= MAX_TIMEOUT:
        raise VisibleDrcArgumentError(
            f"timeout_seconds must be between 1 and {int(MAX_TIMEOUT)}"
        )
    return float(value)


def _bbox(value: Any) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(type(item) not in {int, float} for item in value)
    ):
        raise VisibleDrcError("source_not_captured", "Visible export bbox is invalid")
    if value[2] < value[0] or value[3] < value[1]:
        raise VisibleDrcError("source_not_captured", "Visible export bbox is inverted")
    return list(value)


def _skill_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _default_runner(work_root: Path, calibre_root: Path, timeout: float):
    from sico_datahub.calibre.runner import CalibreRunner

    source_root = Path(__file__).resolve().parents[4]
    return CalibreRunner(
        work_root,
        calibre_root=calibre_root,
        drc_command=source_root / "tools/drc/bin/drc",
        lvs_command=source_root / "tools/lvs/bin/lvs",
        timeout=timeout,
        prepare_timeout=min(1800.0, max(1.0, timeout)),
    )


def _resolve(arguments: dict[str, Any], *, flow_resolver: Callable[..., Any]):
    allowed = {"window_ref", "config_path", "work_root", "calibre_root",
               "timeout_seconds", "previous_job_ref", "region_user_units",
               "auto_previous"}
    unknown = set(arguments) - allowed
    if unknown:
        raise VisibleDrcArgumentError("unknown argument: " + sorted(unknown)[0])
    window_ref = _text(arguments.get("window_ref"), "window_ref", maximum=128)
    if not _WINDOW.fullmatch(window_ref):
        raise VisibleDrcArgumentError("window_ref is invalid")
    config_path = _absolute_file(arguments.get("config_path"), "config_path")
    work_root = _absolute_dir(arguments.get("work_root"), "work_root")
    calibre_root = _absolute_dir(arguments.get("calibre_root"), "calibre_root")
    timeout = _timeout(arguments.get("timeout_seconds", 300))
    try:
        region = export_region(arguments.get("region_user_units"))
    except ValueError as exc:
        raise VisibleDrcArgumentError(str(exc)) from exc
    auto_previous = arguments.get("auto_previous", True)
    if type(auto_previous) is not bool:
        raise VisibleDrcArgumentError("auto_previous must be a boolean")
    previous = arguments.get("previous_job_ref")
    if previous is not None and (not isinstance(previous, str) or _JOB.fullmatch(previous) is None):
        raise VisibleDrcArgumentError("previous_job_ref is invalid")
    try:
        flow = flow_resolver(
            "drc",
            str(config_path),
            str(work_root),
            tool_version="",
        )
    except Exception as exc:
        code = getattr(exc, "code", "invalid_arguments")
        message = getattr(exc, "message", str(exc))
        raise VisibleDrcError(code, message) from exc
    return (window_ref, config_path, work_root, calibre_root, timeout, flow,
            previous, region, auto_previous)


def _export(arguments, *, client, flow_resolver):
    (window_ref, config_path, work_root, calibre_root, timeout, flow,
     previous, region, auto_previous) = _resolve(
        arguments, flow_resolver=flow_resolver
    )
    export_root = work_root / ".sico_datahub" / "visible"
    try:
        export_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise VisibleDrcError("read_failed", "Visible export directory is unavailable") from exc
    run_ref = "visible-" + uuid.uuid4().hex
    output_path = export_root / (run_ref + ".oasis")
    layer_map = flow.layer_map.path if getattr(flow, "layer_map", None) else ""
    code = (
        "aiLayoutExportVisibleOasis("
        + _skill_string(window_ref)
        + " "
        + _skill_string(str(output_path))
        + " "
        + _skill_string(str(work_root))
        + " "
        + _skill_string(str(layer_map))
        + (" " + skill_region(region) if region else "")
        + ")"
    )
    from .skill_result import call_skill

    try:
        ok, export = call_skill(client, code, context="visible-drc")
    except Exception as exc:
        raise VisibleDrcError("internal", "Visible-window export failed") from exc
    if not ok:
        raise VisibleDrcError(
            export.get("code", "read_failed"),
            export.get("message", "Visible-window export failed"),
        )
    source = export.get("source")
    if not isinstance(source, dict) or source.get("cell") != flow.top_cell:
        raise VisibleDrcError(
            "invalid_arguments",
            "Visible layout cell does not match the DRC top cell",
        )
    try:
        candidate = _absolute_file(export.get("path"), "candidate_path")
        bbox = _bbox(export.get("visible_bbox"))
    except VisibleDrcArgumentError as exc:
        raise VisibleDrcError("source_not_captured", str(exc)) from exc
    return {
        "run_ref": run_ref,
        "window_ref": window_ref,
        "config_path": config_path,
        "work_root": work_root,
        "calibre_root": calibre_root,
        "timeout": timeout,
        "flow": flow,
        "candidate": candidate,
        "bbox": bbox,
        "export": export,
        "recheck_of": previous or "",
        "previous_job_ref": previous,
        "region_user_units": region,
        "auto_previous": auto_previous,
        **config_identity(config_path, flow),
    }


class VisibleDrcService:
    """Own and restore async visible-DRC jobs for one MCP process."""

    def __init__(
        self,
        *,
        client,
        runner_factory=None,
        flow_resolver=None,
        workspace=None,
        registry=None,
    ):
        if flow_resolver is None:
            from sico_datahub.calibre.context import resolve_flow

            flow_resolver = resolve_flow
        self.client = client
        self.runner_factory = runner_factory or _default_runner
        self.flow_resolver = flow_resolver
        self.registry = registry
        if self.registry is None and workspace is not None:
            self.registry = VisibleDrcRegistry(workspace)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._closed = False

    def _job(self, job_ref):
        job_ref = _text(job_ref, "job_ref", maximum=64)
        if not _JOB.fullmatch(job_ref):
            raise VisibleDrcArgumentError("job_ref is invalid")
        with self._lock:
            job = self._jobs.get(job_ref)
        return job if job is not None else self._restore(job_ref)

    def _restore(self, job_ref):
        if self._closed:
            raise VisibleDrcError(
                "capability_not_available", "Visible DRC service is closed"
            )
        try:
            job = restore_job(self.registry, self.runner_factory, job_ref)
        except RestoreError as exc:
            raise VisibleDrcError("stale_context", str(exc)) from exc
        with self._lock:
            existing = self._jobs.get(job_ref)
            if existing is not None:
                job.runner.close()
                return existing
            self._jobs[job_ref] = job
        return job

    def start(self, arguments):
        if self._closed:
            raise VisibleDrcError("capability_not_available", "Visible DRC service is closed")
        return self._start_values(_export(
            arguments,
            client=self.client,
            flow_resolver=self.flow_resolver,
        ))

    def _start_values(self, values):
        if self.registry is not None:
            try:
                self.registry.save(record_from_values(values))
                self.registry.append_event(
                    values["run_ref"], "queued", "Visible DRC queued"
                )
            except Exception as exc:
                discard_job(self.registry, values["run_ref"])
                raise VisibleDrcError(
                    "read_failed", "Visible DRC job registry is unavailable"
                ) from exc
        try:
            runner = self.runner_factory(
                str(values["work_root"]),
                str(values["calibre_root"]),
                values["timeout"],
            )
            receipt = runner.submit_candidate(
                values["run_ref"],
                str(values["config_path"]),
                str(values["work_root"]),
                str(values["candidate"]),
                values["bbox"],
                kind="drc",
            )
        except Exception as exc:
            discard_job(self.registry, values["run_ref"])
            if "runner" in locals():
                runner.close()
            raise VisibleDrcError(
                getattr(exc, "code", "capability_not_available"),
                getattr(exc, "message", "Visible-window DRC could not start"),
            ) from exc
        job = Job(values=values, runner=runner)
        with self._lock:
            self._jobs[values["run_ref"]] = job
        start_cancel_watch(self, values["run_ref"])
        payload = response_payload(job, receipt, schema="cad.visible_drc.start.v1")
        return with_events(self.registry, payload, values["run_ref"])

    def get(self, job_ref, after=0):
        if type(after) is not int or not 0 <= after <= 2**31 - 1:
            raise VisibleDrcArgumentError("after is invalid")
        job = self._job(job_ref)
        with self._lock:
            if job.cached is not None:
                clear_cancel(self.registry, job_ref)
                return with_events(self.registry, dict(job.cached), job_ref, after)
        if cancel_requested(self.registry, job_ref):
            if job.restored:
                clear_cancel(self.registry, job_ref)
            else:
                return self.cancel(job_ref)
        receipt = job.runner.status(job.values["run_ref"])
        remember_state(self.registry, job.values["run_ref"], receipt.state,
                       getattr(receipt, "message", ""))
        payload = response_payload(job, receipt, schema="cad.visible_drc.status.v1")
        if receipt.terminal:
            try:
                _receipt, result = job.runner.result(job.values["run_ref"])
                payload["result"] = result
            except Exception as exc:
                payload["result_error"] = {
                    "code": getattr(exc, "code", "source_not_captured"),
                    "message": getattr(exc, "message", "Visible DRC result is unavailable"),
                }
            job.runner.close()
            payload["terminal"] = True
            with self._lock:
                job.cached = dict(payload)
        return with_events(self.registry, payload, job_ref, after)

    def cancel(self, job_ref):
        job = self._job(job_ref)
        if job.restored:
            clear_cancel(self.registry, job_ref)
            return self.get(job_ref)
        with self._lock:
            if job.cached is not None:
                clear_cancel(self.registry, job_ref)
                return dict(job.cached)
        receipt = job.runner.cancel(job.values["run_ref"])
        payload = response_payload(job, receipt, schema="cad.visible_drc.status.v1")
        if receipt.terminal:
            remember_state(self.registry, job.values["run_ref"], receipt.state,
                           getattr(receipt, "message", ""))
            job.runner.close()
            payload["terminal"] = True
            with self._lock:
                job.cached = dict(payload)
            clear_cancel(self.registry, job_ref)
        return with_events(self.registry, payload, job.values["run_ref"])

    def list_jobs(self, arguments):
        try:
            return service_list_jobs(self.registry, self._closed, arguments)
        except RuntimeError as exc:
            raise VisibleDrcError("capability_not_available", str(exc)) from exc
        except ValueError as exc:
            raise VisibleDrcArgumentError(str(exc)) from exc

    def _previous(self, job_ref):
        if not job_ref:
            return None
        job = self._job(job_ref)
        previous = self.get(job_ref)
        if previous.get("terminal") is not True or "result" not in previous:
            raise VisibleDrcError(
                "stale_context", "Previous visible DRC result is unavailable"
            )
        return {"job": job, "payload": previous}

    def check(self, arguments, *, sleep=time.sleep, clock=time.monotonic):
        if self._closed:
            raise VisibleDrcError(
                "capability_not_available", "Visible DRC service is closed"
            )
        values = _export(
            arguments,
            client=self.client,
            flow_resolver=self.flow_resolver,
        )
        previous = self._previous(values.get("previous_job_ref"))
        if previous is None and values.get("auto_previous") and self.registry is not None:
            record = self.registry.latest(values)
            if record is not None:
                try:
                    previous = self._previous(record["job_ref"])
                except VisibleDrcError:
                    previous = None
        if previous is not None:
            values["recheck_of"] = previous["job"].values["run_ref"]
        basis = None
        if previous is not None:
            reusable, basis = reuse_basis(
                previous["payload"], previous["job"].values, values
            )
            if reusable:
                discard_export(
                    values, previous["payload"]["export"].get("path")
                )
                reused = dict(previous["payload"])
                reused["schema"] = "cad.visible_drc.v1"
                reused["reused"] = True
                reused["previous_job_ref"] = previous["job"].values["run_ref"]
                reused["delta"] = delta(
                    reused["result"], reused["result"], "reused:" + basis
                )
                return True, reused
        started = self._start_values(values)
        deadline = clock() + float(arguments.get("timeout_seconds", 300))
        while True:
            payload = self.get(started["job_ref"])
            if payload.get("terminal"):
                if payload["job"]["state"] == "cancelled":
                    raise VisibleDrcError("cancelled", "Visible-window DRC was cancelled")
                payload["schema"] = "cad.visible_drc.v1"
                payload["reused"] = False
                if previous is not None and "result" in payload:
                    payload["delta"] = delta(
                        previous["payload"]["result"],
                        payload["result"],
                        basis,
                    )
                return True, payload
            if clock() >= deadline:
                self.cancel(started["job_ref"])
                raise VisibleDrcError("timeout", "Visible-window DRC timed out")
            sleep(0.1)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            job.runner.close()

def check_visible_layout_drc(
    arguments: dict[str, Any],
    *,
    client,
    runner_factory=None,
    flow_resolver=None,
    sleep=time.sleep,
    clock=time.monotonic,
):
    """Synchronous compatibility facade over one async visible-DRC job."""
    if not isinstance(arguments, dict):
        raise VisibleDrcArgumentError("arguments must be an object")
    service = VisibleDrcService(
        client=client,
        runner_factory=runner_factory,
        flow_resolver=flow_resolver,
    )
    try:
        return service.check(arguments, sleep=sleep, clock=clock)
    finally:
        service.close()


__all__ = [
    "CANCEL_NAME",
    "GET_NAME",
    "LIST_NAME",
    "NAME",
    "NAMES",
    "START_NAME",
    "VisibleDrcArgumentError",
    "VisibleDrcError",
    "VisibleDrcService",
    "check_visible_layout_drc",
]
