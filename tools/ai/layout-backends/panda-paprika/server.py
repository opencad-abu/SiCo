"""stdio JSON-RPC adapter for the external PANDA Paprika backend."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from engine import Config, EngineError, execute, validate

BACKEND_ID = "panda-paprika"
BACKEND_VERSION = "0.1.0"
API_VERSION = "1.0"
CAPABILITIES = {
    "source_kinds": ["netlist"],
    "actions": {
        "place": {
            "source_kinds": ["netlist"],
            "requires": ["netlist", "techfile", "stdcell", "pcell_bundle",
                         "paramfile", "process_view"],
            "produces": ["placement_layout"],
            "constraint_kinds": ["symmetry"],
        },
    },
    "artifact_formats": ["gds", "json"],
    "catalogs": [],
    "process_view_schema": "sico.process_view.v1",
}


class Job:
    def __init__(self, request: dict[str, Any], config: Config, workspace: Path) -> None:
        self.request = request
        self.config = config
        self.workspace = workspace
        self.job_id = request.get("job_id", "")
        self.state = "queued"
        self.diagnostics: list[dict[str, Any]] = []
        self.artifacts: list[dict[str, Any]] = []
        self.error: str | None = None
        self.process_ref: list[Any] = []
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        with self.lock:
            self.state = "running"
        try:
            artifacts = execute(
                self.request, self.config, self.workspace,
                process_ref=self.process_ref,
            )
            with self.lock:
                self.artifacts = artifacts
                self.state = "succeeded"
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
                self.state = "failed"
                self.diagnostics.append({
                    "code": getattr(exc, "code", "internal_error"),
                    "message": str(exc),
                    "stage": "engine",
                })

    def status(self) -> dict[str, Any]:
        with self.lock:
            stage = "place_and_route" if self.request.get("action") == "place_and_route" else (
                self.request.get("action", "place")
            )
            return {
                "job_id": self.job_id,
                "state": self.state,
                "stages": [{"name": stage, "state": self.state, "progress": 1.0
                            if self.state == "succeeded" else 0.0}],
                "artifacts": [],
                "diagnostics": list(self.diagnostics),
                "log_tail": self._log_tail(),
            }

    def _log_tail(self) -> str:
        text = ""
        for name in ("run_place.log", "run_route.log"):
            path = self.workspace / "output" / name
            try:
                text += path.read_text(encoding="utf-8", errors="replace")[-4000:]
            except OSError:
                pass
        return text[-6000:]

    def collect(self) -> dict[str, Any]:
        with self.lock:
            if self.state != "succeeded":
                raise EngineError("job_not_succeeded", "PANDA job is not succeeded")
            return {
                "schema": "sico.layout.result.v1",
                "job_id": self.job_id,
                "state": self.state,
                "artifacts": list(self.artifacts),
                "engine_checks": [],
                "diagnostics": list(self.diagnostics),
                "provenance": {
                    "backend_id": BACKEND_ID,
                    "backend_version": BACKEND_VERSION,
                },
            }

    def cancel(self) -> dict[str, Any]:
        with self.lock:
            self.state = "cancelled"
        for process in list(self.process_ref):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, 15)
                except OSError:
                    pass
        return self.status()


def _reply(request_id: Any, result: dict[str, Any]) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result},
                     ensure_ascii=False), flush=True)


def _error(request_id: Any, code: int, message: str) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": request_id,
                      "error": {"code": code, "message": message}},
                     ensure_ascii=False), flush=True)


def _config() -> Config:
    return Config.load(Path(os.environ["SICO_PANDA_CONFIG"])
                       if "SICO_PANDA_CONFIG" in os.environ else None)


def main() -> int:
    jobs: dict[str, Job] = {}
    for line in __import__("sys").stdin.buffer:
        request: dict[str, Any] = {}
        try:
            request = json.loads(line)
            request_id = request["id"]
            method = request["method"]
            params = request.get("params") or {}
            if method == "backend.describe":
                try:
                    config = _config()
                    missing = [str(path) for path in
                               (config.placement, config.routing,
                                config.placement_runner, config.routing_runner)
                               if not path.is_file()]
                except EngineError as exc:
                    config = None
                    missing = [str(exc)]
                _reply(request_id, {
                    "schema": "sico.layout.backend.v1",
                    "id": BACKEND_ID,
                    "version": BACKEND_VERSION,
                    "api_version": API_VERSION,
                    "capabilities": CAPABILITIES,
                    "process_views": [],
                    "catalogs": [],
                    "ready": not missing,
                    "diagnostics": [{"code": "engine_unavailable", "message": path}
                                    for path in missing],
                })
            elif method == "backend.validate":
                try:
                    _reply(request_id, validate(params, _config()))
                except EngineError as exc:
                    _reply(request_id, {
                        "ok": True, "accepted": False,
                        "unsupported_required": [],
                        "unsupported_preferred": [exc.code],
                        "diagnostics": [{"code": exc.code, "message": str(exc)}],
                        "required_inputs": [],
                        "estimated_resources": {},
                    })
            elif method == "job.start":
                job_id = params.get("job_id")
                workspace = Path(params.get("workspace", ""))
                if not isinstance(job_id, str) or not workspace.is_absolute():
                    raise ValueError("invalid job request")
                if params.get("action") not in CAPABILITIES["actions"]:
                    raise EngineError(
                        "unsupported_action",
                        "action is not advertised by this backend: "
                        + str(params.get("action")),
                    )
                job = Job(params, _config(), workspace)
                jobs[job_id] = job
                job.start()
                _reply(request_id, {
                    "job_id": job_id, "state": "queued", "workspace": str(workspace),
                })
            elif method in {"job.status", "job.collect", "job.cancel"}:
                job = jobs.get(params.get("job_id"))
                if job is None:
                    _error(request_id, -32001, "job not found")
                elif method == "job.status":
                    _reply(request_id, job.status())
                elif method == "job.collect":
                    _reply(request_id, job.collect())
                else:
                    _reply(request_id, job.cancel())
            elif method == "backend.shutdown":
                for job in jobs.values():
                    job.cancel()
                _reply(request_id, {"ok": True})
                return 0
            else:
                _error(request_id, -32601, "unknown method")
        except EngineError as exc:
            _error(request.get("id", -1), -32000, str(exc))
        except Exception as exc:
            _error(request.get("id", -1), -32603, str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
