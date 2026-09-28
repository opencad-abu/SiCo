"""Shared defaults fixtures inputs."""

from __future__ import annotations
import json
from pathlib import Path
from mtsnetlistor.model import SourceDesign


def _protected_probe_source():
    root = Path(__file__).resolve().parents[1] / "skill"
    return (root / "MTS_defaultsWorker.il").read_text() + (root / "MTS_probeWorker.il").read_text()


def _source(tmp_path: Path) -> SourceDesign:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    return SourceDesign(cds, "source", "inv", "schematic")


def _report(tmp_path: Path) -> Path:
    source = _source(tmp_path)
    value = {
        "schema_version": 1,
        "status": "succeeded",
        "dialect": "spectre",
        "tool_name": "spectre",
        "source": {
            "cds_lib": str(source.cds_lib.resolve()),
            "library": "source",
            "cell": "inv",
            "view": "schematic",
        },
        "baseline": {
            "model_files": [],
            "environment_options": [],
            "simulator_options": [
                ["reltol", [["value", "1e-3"], ["choices", None]]],
                ["maxwarns", [["value", "5"], ["choices", None]]],
            ],
        },
        "after_design": {
            "model_files": [["#/tmp/models.scs", "tt"]],
            "environment_options": [["temp", "27.000"], ["scale", "1e-6"]],
            "simulator_options": [
                ["reltol", [["value", 0.001]]],
                ["gmin", [["value", "1e-12"], ["choices", None]]],
                ["maxwarns", [["value", "5"], ["choices", None]]],
            ],
        },
        "after_startup_simrc": {},
        "diagnostics": [],
        "api_errors": [],
    }
    path = tmp_path / "defaults.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _report_with_snapshot(tmp_path: Path, snapshot: dict[str, object]) -> Path:
    path = _report(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["after_design"] = snapshot
    value["after_startup_simrc"] = {}
    path.write_text(json.dumps(value), encoding="utf-8")
    return path
