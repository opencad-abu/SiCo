"""Deterministic fake backend used to qualify the layout host contract."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA = "sico.layout.backend.v1"
CAPABILITIES = {
    "source_kinds": ["generator", "netlist"],
    "actions": {
        "synthesize": {
            "source_kinds": ["generator", "netlist"],
            "requires": [],
            "produces": ["layout"],
            "constraint_kinds": ["symmetry", "guard_ring", "density"],
        },
    },
    "artifact_formats": ["gds", "json"],
    "catalogs": ["generator"],
    "process_view_schema": "sico.process_view.v1",
}


def _reply(request_id: int, result: dict[str, Any]) -> None:
    payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _error(request_id: int, code: int, message: str) -> None:
    payload = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _artifact(workspace: Path, name: str, raw: bytes) -> dict[str, Any]:
    path = workspace / "output" / name
    path.write_bytes(raw)
    return {
        "role": "layout",
        "path": "output/" + name,
        "format": "gds",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
    }


def _start(request: dict[str, Any]) -> dict[str, Any]:
    job_id = request.get("job_id")
    workspace = Path(request.get("workspace", ""))
    if not isinstance(job_id, str) or not workspace.is_absolute():
        raise ValueError("invalid job request")
    if request.get("action") != "synthesize":
        raise ValueError("unsupported action")
    source = request.get("source")
    if not isinstance(source, dict) or source.get("kind") not in {"generator", "netlist"}:
        raise ValueError("unsupported source")
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "output").mkdir(exist_ok=True)
    layout = _artifact(workspace, "layout.gds", b"FAKE-GDS\n")
    manifest = {
        "schema": "sico.layout.geometry.v1",
        "top": "FAKE",
        "format": "gds",
        "dbu_per_um": 1000,
        "bbox_um": [0, 0, 1, 1],
        "hierarchy": "flat",
        "stage": "synthesize",
    }
    (workspace / "output" / "geometry_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    state = "running" if (request.get("options") or {}).get("fake_hold") else "succeeded"
    if (request.get("options") or {}).get("fake_escape"):
        layout["path"] = "../escape.gds"
    return {"job_id": job_id, "state": state, "workspace": str(workspace), "artifacts": [layout]}


def main() -> int:
    jobs: dict[str, dict[str, Any]] = {}
    for line in sys.stdin.buffer:
        request = {}
        try:
            request = json.loads(line)
            request_id = request["id"]
            method = request["method"]
            params = request.get("params") or {}
            if method == "backend.describe":
                _reply(request_id, {
                    "schema": SCHEMA, "id": "fake-layout", "version": "0.1.0",
                    "api_version": "1.0", "capabilities": CAPABILITIES,
                    "process_views": ["fake/1p10m"], "catalogs": ["generator"],
                    "ready": True, "diagnostics": [],
                })
            elif method == "backend.validate":
                action = params.get("action")
                source = params.get("source") or {}
                accepted = action == "synthesize" and source.get("kind") in {"generator", "netlist"}
                _reply(request_id, {
                    "ok": True, "accepted": accepted,
                    "unsupported_required": [] if accepted else ["action/source"],
                    "unsupported_preferred": [], "diagnostics": [],
                    "required_inputs": [], "estimated_resources": {"cpu": 1, "memory_gb": 1},
                })
            elif method == "job.start":
                job = _start(params)
                jobs[job["job_id"]] = job
                _reply(request_id, job)
            elif method == "job.status":
                job = jobs.get(params.get("job_id"))
                if job is None:
                    _error(request_id, -32001, "job not found")
                else:
                    _reply(request_id, {
                        "job_id": job["job_id"], "state": job["state"],
                        "stages": [{"name": "synthesize", "state": job["state"], "progress": 1.0}],
                        "artifacts": [], "diagnostics": [], "log_tail": "",
                    })
            elif method == "job.collect":
                job = jobs.get(params.get("job_id"))
                if job is None:
                    _error(request_id, -32001, "job not found")
                elif job["state"] != "succeeded":
                    _error(request_id, -32002, "job is not succeeded")
                else:
                    _reply(request_id, {
                        "schema": "sico.layout.result.v1", "job_id": job["job_id"],
                        "state": job["state"], "artifacts": job["artifacts"],
                        "engine_checks": [], "diagnostics": [],
                        "provenance": {"backend_id": "fake-layout", "backend_version": "0.1.0"},
                    })
            elif method == "job.cancel":
                job = jobs.get(params.get("job_id"))
                if job is None:
                    _error(request_id, -32001, "job not found")
                else:
                    job["state"] = "cancelled"
                    _reply(request_id, {
                        "job_id": job["job_id"], "state": "cancelled",
                        "stages": [{"name": "synthesize", "state": "cancelled", "progress": 0.0}],
                        "artifacts": [], "diagnostics": [], "log_tail": "",
                    })
            elif method == "backend.shutdown":
                _reply(request_id, {"ok": True})
                return 0
            else:
                _error(request_id, -32601, "unknown method")
        except Exception as exc:
            _error(request.get("id", -1) if isinstance(request, dict) else -1, -32603, str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
