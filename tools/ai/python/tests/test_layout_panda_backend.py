import json
import stat
import sys
from pathlib import Path

from cadai.layout.host import LayoutBackendHost


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fixture(tmp_path):
    root = tmp_path / "panda"
    (root / "pr_kernel").mkdir(parents=True)
    (root / "pr_kernel/placement").write_text("fake", encoding="utf-8")
    (root / "pr_kernel/routing").write_text("fake", encoding="utf-8")
    (root / "run_placement.sh").write_text(
        "\n".join([
            "#!/bin/bash",
            "set -e",
            'mkdir -p "$PLACE_RESULT"',
            'printf "placement" > "$PLACE_RESULT/top.gds"',
            'printf "netlist=%s\\n" "$PLACE_NETLIST" > "$PLACE_LOG"',
        ]) + "\n", encoding="utf-8")
    (root / "run_route.sh").write_text(
        "\n".join([
            "#!/bin/bash",
            "set -e",
            'mkdir -p "$ROUTE_RESULT"',
            'printf "route" > "$ROUTE_RESULT/analog_agent.route.gds"',
            'printf "netlist=%s\\n" "$ROUTE_NETLIST" > "$ROUTE_LOG"',
        ]) + "\n", encoding="utf-8")
    for name in ("placement", "routing"):
        (root / "pr_kernel" / name).chmod(0o755)
    (root / "run_placement.sh").chmod(0o755)
    (root / "run_route.sh").chmod(0o755)
    config = tmp_path / "panda-config.json"
    config.write_text(json.dumps({"panda_root": str(root)}), encoding="utf-8")
    manifest_path = tmp_path / "backend.json"
    manifest = {
        "schema": "sico.layout.backend.v1", "id": "panda-paprika",
        "name": "PANDA fixture", "version": "0.1.0", "api_version": "1.0",
        "transport": {
            "kind": "stdio",
            "command": [sys.executable, str(Path(__file__).parents[2] /
                         "layout-backends/panda-paprika/server.py")],
            "cwd": str(Path(__file__).parents[2] / "layout-backends/panda-paprika"),
        },
        "capabilities": {
            "source_kinds": ["netlist"],
            "actions": {"place": {
                "source_kinds": ["netlist"],
                "requires": ["netlist", "techfile", "stdcell", "pcell_bundle",
                             "paramfile", "process_view"],
                "produces": ["placement_layout"],
                "constraint_kinds": ["symmetry"],
            }},
            "process_view_schema": "sico.process_view.v1",
        },
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, config


def _request(tmp_path):
    inputs = tmp_path / "inputs"
    (inputs / "pcell").mkdir(parents=True)
    for name in ("top.sp", "lef.json", "stdcell.json", "allconfig.json"):
        (inputs / name).write_text("{}\n", encoding="utf-8")
    (inputs / "process_view.json").write_text(json.dumps({
        "schema": "sico.process_view.v1",
        "database_unit": 1e-9,
        "dbu_per_um": 1000,
        "stream_unit_m": 1e-6,
        "stream_precision_m": 1e-9,
        "grid_um": 0.005,
    }), encoding="utf-8")
    return {
        "schema": "sico.layout.job.v1",
        "job_id": "job-panda",
        "backend_id": "panda-paprika",
        "action": "place",
        "source": {"kind": "netlist", "path": str(inputs / "top.sp")},
        "process_view_ref": "smic18/process",
        "inputs": {
            "netlist": str(inputs / "top.sp"),
            "techfile": str(inputs / "lef.json"),
            "stdcell": str(inputs / "stdcell.json"),
            "paramfile": str(inputs / "allconfig.json"),
            "pcell_bundle": str(inputs / "pcell"),
            "process_view": str(inputs / "process_view.json"),
        },
        "constraints": {},
        "outputs": {"requested_roles": ["placement_layout"]},
        "options": {},
        "resources": {},
        "timeout_sec": 30,
    }


def test_panda_backend_orchestrates_place(tmp_path, monkeypatch):
    manifest_path, config = _fixture(tmp_path)
    monkeypatch.setenv("SICO_PANDA_CONFIG", str(config))
    host = LayoutBackendHost(json.loads(manifest_path.read_text()), tmp_path / "jobs")
    with host.start(_request(tmp_path)) as job:
        for _ in range(100):
            status = job.status()
            if status["state"] in {"succeeded", "failed", "cancelled"}:
                break
            __import__("time").sleep(.05)
        assert status["state"] == "succeeded", status
        result = job.collect()
    roles = {row["role"] for row in result["artifacts"]}
    assert roles >= {"placement_layout", "report", "log"}
    manifest_path = Path(result["artifacts"][0]["metadata"]["geometry_manifest"])
    # metadata points at workspace-relative path; resolve through the job root
    manifest = json.loads((job_workspace := Path(tmp_path / "jobs") /
                           result["job_id"] / manifest_path).read_text())
    assert manifest["schema"] == "sico.layout.geometry.v1"
