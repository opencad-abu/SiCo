import sys

import pytest

from cadai.layout.host import HostError, LayoutBackendHost
from cadai.layout.registry import ManifestError, validate_manifest


def manifest(tmp_path):
    return {
        "schema": "sico.layout.backend.v1",
        "id": "fake-layout",
        "name": "Fake Layout Backend",
        "version": "0.1.0",
        "api_version": "1.0",
        "transport": {
            "kind": "stdio",
            "command": [sys.executable, "-m", "cadai.layout.fake_backend"],
            "cwd": str(tmp_path),
        },
        "capabilities": {
            "source_kinds": ["generator", "netlist"],
            "actions": {
                "synthesize": {
                    "source_kinds": ["generator", "netlist"],
                    "requires": [],
                    "produces": ["layout"],
                    "constraint_kinds": ["symmetry", "guard_ring", "density"],
                }
            },
            "artifact_formats": ["gds", "json"],
            "catalogs": ["generator"],
            "process_view_schema": "sico.process_view.v1",
        },
        "license": {"spdx": "Apache-2.0", "commercial_use": "allowed"},
        "integrity": {"files": {}},
    }


def request(tmp_path, job_id="job1", action="synthesize", options=None):
    return {
        "schema": "sico.layout.job.v1",
        "job_id": job_id,
        "backend_id": "fake-layout",
        "action": action,
        "source": {
            "kind": "generator",
            "generator_id": "fake.mirror",
            "parameters": {},
        },
        "process_view_ref": "fake/1p10m",
        "inputs": {},
        "constraints": {},
        "outputs": {"requested_roles": ["layout"], "layout_format": "gds"},
        "options": options or {},
        "resources": {},
        "timeout_sec": 10,
    }


def test_fake_backend_round_trip_and_artifact_verification(tmp_path):
    host = LayoutBackendHost(manifest(tmp_path), tmp_path / "jobs")
    described = host.describe()
    assert described["ready"] is True
    assert described["id"] == "fake-layout"

    validated = host.validate(request(tmp_path))
    assert validated["accepted"] is True

    with host.start(request(tmp_path)) as job:
        assert job.status()["state"] == "succeeded"
        result = job.collect()
        assert result["state"] == "succeeded"
        assert result["artifacts"][0]["role"] == "layout"
        assert (job.workspace / result["artifacts"][0]["path"]).is_file()


def test_fake_backend_reports_unsupported_action(tmp_path):
    host = LayoutBackendHost(manifest(tmp_path), tmp_path / "jobs")
    result = host.validate(request(tmp_path, action="place_and_route"))
    assert result["accepted"] is False
    assert result["unsupported_required"]


def test_host_rejects_artifact_path_escape(tmp_path):
    host = LayoutBackendHost(manifest(tmp_path), tmp_path / "jobs")
    with host.start(request(tmp_path, options={"fake_escape": True})) as job:
        with pytest.raises(HostError, match="escapes workspace"):
            job.collect()


def test_host_cancels_running_fake_job(tmp_path):
    host = LayoutBackendHost(manifest(tmp_path), tmp_path / "jobs")
    with host.start(request(tmp_path, options={"fake_hold": True})) as job:
        assert job.status()["state"] == "running"
        assert job.cancel()["state"] == "cancelled"
        assert job.status()["state"] == "cancelled"


def test_manifest_rejects_unsupported_api_version(tmp_path):
    value = manifest(tmp_path)
    value["api_version"] = "2.0"
    with pytest.raises(ManifestError, match="API version"):
        validate_manifest(value)
