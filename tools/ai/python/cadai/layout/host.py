"""Out-of-process layout backend host for the v1 draft contract."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .protocol import JsonRpcProcess
from .registry import validate_manifest

JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class HostError(RuntimeError):
    pass


class BackendJob:
    def __init__(self, host: LayoutBackendHost, process: JsonRpcProcess,
                 job_id: str, workspace: Path) -> None:
        self.host = host
        self.process = process
        self.job_id = job_id
        self.workspace = workspace
        self.closed = False

    def status(self, *, timeout: float | None = None) -> dict[str, Any]:
        return self.process.call("job.status", {"job_id": self.job_id}, timeout=timeout)

    def cancel(self, *, timeout: float | None = None) -> dict[str, Any]:
        return self.process.call("job.cancel", {"job_id": self.job_id}, timeout=timeout)

    def collect(self, *, timeout: float | None = None) -> dict[str, Any]:
        result = self.process.call("job.collect", {"job_id": self.job_id}, timeout=timeout)
        self._verify_artifacts(result)
        return result

    def _verify_artifacts(self, result: dict[str, Any]) -> None:
        artifacts = result.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise HostError("backend result has no artifacts")
        root = self.workspace.resolve()
        for row in artifacts:
            if not isinstance(row, dict):
                raise HostError("artifact entry must be an object")
            for key in ("role", "path", "sha256"):
                if not isinstance(row.get(key), str) or not row[key]:
                    raise HostError("artifact missing " + key)
            path = PurePosixPath(row["path"])
            if path.is_absolute() or any(
                    part in {"", ".", ".."} for part in row["path"].split("/")):
                raise HostError("artifact path escapes workspace")
            target = (root / path).resolve()
            if root not in target.parents or not target.is_file():
                raise HostError("artifact is missing or outside workspace")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if row["sha256"] != digest:
                raise HostError("artifact digest mismatch")
            if "size" in row and row["size"] != target.stat().st_size:
                raise HostError("artifact size mismatch")

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.process.close()

    def __enter__(self) -> BackendJob:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class LayoutBackendHost:
    def __init__(self, manifest: dict[str, Any], workspace_root: str | Path,
                 *, timeout: float = 30.0) -> None:
        self.manifest = validate_manifest(manifest)
        self.workspace_root = Path(workspace_root)
        self.timeout = timeout
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def _process(self) -> JsonRpcProcess:
        transport = self.manifest["transport"]
        return JsonRpcProcess(transport["command"], cwd=Path(transport["cwd"]),
                              timeout=self.timeout)

    def describe(self, *, timeout: float | None = None) -> dict[str, Any]:
        with self._process() as process:
            return process.call("backend.describe", {}, timeout=timeout)

    def validate(self, request: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        with self._process() as process:
            return process.call("backend.validate", request, timeout=timeout)

    def start(self, request: dict[str, Any], *, timeout: float | None = None) -> BackendJob:
        if not isinstance(request, dict):
            raise HostError("job request must be an object")
        job_id = request.get("job_id")
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise HostError("invalid job_id")
        if request.get("backend_id") not in (None, self.manifest["id"]):
            raise HostError("backend_id does not match manifest")
        workspace = (self.workspace_root / job_id).resolve()
        if workspace.exists():
            raise HostError("job workspace already exists")
        for name in ("input", "work", "output"):
            (workspace / name).mkdir(parents=True, exist_ok=True)
        request = dict(request)
        request["backend_id"] = self.manifest["id"]
        request["workspace"] = str(workspace)
        process = self._process()
        try:
            result = process.call("job.start", request, timeout=timeout)
        except Exception:
            process.close()
            raise
        if result.get("job_id") != job_id:
            process.close()
            raise HostError("backend returned a different job_id")
        return BackendJob(self, process, job_id, workspace)
