"""Project-scoped backend registry and in-memory job service (v1 draft)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from .host import LayoutBackendHost
from .import_plan import build_import_plan, import_status
from .registry import BackendRegistry


class LayoutServiceError(RuntimeError):
    pass


def _copy_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LayoutServiceError("layout request must be an object")
    request = dict(value)
    for key in ("job_id", "workspace", "backend_id"):
        request.pop(key, None)
    action = request.get("action")
    source = request.get("source")
    if not isinstance(action, str) or not action:
        raise LayoutServiceError("layout request requires action")
    if not isinstance(source, dict):
        raise LayoutServiceError("layout request requires source")
    for key in ("inputs", "constraints", "options", "resources"):
        if key in request and not isinstance(request[key], dict):
            raise LayoutServiceError(key + " must be an object")
    if "outputs" in request and not isinstance(request["outputs"], dict):
        raise LayoutServiceError("outputs must be an object")
    request.setdefault("schema", "sico.layout.job.v1")
    request.setdefault("inputs", {})
    request.setdefault("constraints", {})
    request.setdefault("options", {})
    request.setdefault("resources", {})
    request.setdefault("outputs", {"requested_roles": ["layout"]})
    return request


class LayoutService:
    """One project's backends and active in-process job handles."""

    def __init__(self, workspace: str | Path, *, backends_root: str | Path | None = None,
                 jobs_root: str | Path | None = None) -> None:
        self.workspace = Path(workspace)
        self.backends_root = Path(backends_root) if backends_root is not None else (
            self.workspace / ".sico/layout/backends")
        self.jobs_root = Path(jobs_root) if jobs_root is not None else (
            self.workspace / ".sico/layout/jobs")
        self.registry = BackendRegistry(self.backends_root)
        self.jobs: dict[str, dict[str, Any]] = {}

    def list_backends(self) -> dict[str, Any]:
        self.registry.reload()
        return self.registry.list()

    def get_backend(self, backend_id: str) -> dict[str, Any]:
        self.registry.reload()
        return self.registry.info(backend_id)

    def preview(self, backend_id: str, request: dict[str, Any]) -> dict[str, Any]:
        self.registry.reload()
        manifest = self.registry.get(backend_id)
        checked = _copy_request(request)
        checked["backend_id"] = backend_id
        checked["job_id"] = "preview-" + uuid.uuid4().hex
        preview_dir = self.jobs_root / checked["job_id"]
        for name in ("input", "work", "output"):
            (preview_dir / name).mkdir(parents=True, exist_ok=True)
        checked["workspace"] = str(preview_dir)
        host = LayoutBackendHost(manifest, self.jobs_root)
        return host.validate(checked)

    def start(self, backend_id: str, request: dict[str, Any]) -> dict[str, Any]:
        self.registry.reload()
        manifest = self.registry.get(backend_id)
        checked = _copy_request(request)
        job_id = "layout-" + uuid.uuid4().hex
        checked["backend_id"] = backend_id
        checked["job_id"] = job_id
        host = LayoutBackendHost(manifest, self.jobs_root)
        job = host.start(checked)
        self.jobs[job_id] = {
            "job": job, "status": None, "result": None,
            "workspace": job.workspace,
        }
        return {
            "job_id": job_id,
            "state": "queued",
            "workspace": str(job.workspace),
        }

    def _record(self, job_id: str) -> dict[str, Any]:
        record = self.jobs.get(job_id)
        if record is None:
            raise LayoutServiceError("unknown layout job: " + job_id)
        return record

    def status(self, job_id: str) -> dict[str, Any]:
        record = self._record(job_id)
        if record["result"] is not None:
            return {**record["status"], "result": record["result"], "collected": True}
        if record["job"] is None:
            return record["status"]
        status = record["job"].status()
        record["status"] = status
        if status.get("state") == "succeeded":
            record["result"] = record["job"].collect()
            record["job"].close()
            record["job"] = None
            return {**status, "result": record["result"], "collected": True}
        if status.get("state") in {"failed", "cancelled"}:
            record["job"].close()
            record["job"] = None
        return status

    def cancel(self, job_id: str) -> dict[str, Any]:
        record = self._record(job_id)
        if record["job"] is None:
            return record["status"]
        status = record["job"].cancel()
        record["status"] = status
        record["job"].close()
        record["job"] = None
        return status

    def plan_import(self, job_id: str, *, target_library: str, target_cell: str,
                    target_view: str, layer_map: str) -> dict[str, Any]:
        record = self._record(job_id)
        if record["result"] is None:
            self.status(job_id)
        result = record.get("result")
        if not isinstance(result, dict):
            raise LayoutServiceError("layout job has no collected result")
        return build_import_plan(
            result,
            workspace=record["workspace"],
            target_library=target_library,
            target_cell=target_cell,
            target_view=target_view,
            layer_map=layer_map,
            expected_job_id=job_id,
        )

    def import_status(self, plan_file: str) -> dict[str, Any]:
        return import_status(plan_file, workspace=self.workspace)

    def close(self) -> None:
        for record in self.jobs.values():
            job = record.get("job")
            if job is not None:
                job.close()
                record["job"] = None
