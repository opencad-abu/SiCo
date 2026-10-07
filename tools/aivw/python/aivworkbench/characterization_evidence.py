"""Re-authenticate source characterization before fitting or replay comparison."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .electrical_experiment import read_experiment
from .manifest import verify_split_manifests
from .spectre_data import measure_csv
from .workspace import sha256_file, stable_digest


def read_characterization(control_path: Path) -> dict[str, Any]:
    control = json.loads(control_path.read_text())
    root = Path(control["details"]["payload_root"])
    pair = verify_split_manifests(control_path, root / "payload-manifest.json")
    if pair["run_status"] != "PASS":
        raise ValueError("characterization run did not execute successfully")
    payload = json.loads((root / "payload-manifest.json").read_text())
    indexed = {item["path"]: item for item in payload["artifacts"]}
    matches = [g for g in control["details"]["gate_results"].values()
               if g["executor"] == "spectre.block_characterization"]
    if len(matches) != 1 or matches[0]["status"] != "PASS":
        raise ValueError("one successful characterization producer is required")
    gate = matches[0]

    def artifact(relative, digest=None):
        if relative not in indexed or relative not in gate["artifacts"]:
            raise ValueError("characterization artifact is not indexed by its producer")
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("unsafe characterization artifact")
        path = root / rel
        if any((root / Path(*rel.parts[:i])).is_symlink() for i in range(1,len(rel.parts)+1)):
            raise ValueError("characterization artifact traverses a symlink")
        if digest is not None and sha256_file(path) != digest:
            raise ValueError("characterization artifact digest differs")
        return path

    evidence_path = artifact(gate["outputs"]["characterization_evidence"], gate["outputs"]["characterization_evidence_sha256"])
    evidence = json.loads(evidence_path.read_text())
    parent = evidence_path.parent.relative_to(root)
    plan_path = artifact((parent/"experiment.json").as_posix(), evidence["experiment_sha256"])
    plan = read_experiment(plan_path)
    if stable_digest(plan) != evidence["experiment_digest"] or plan["target"] != evidence["target"]:
        raise ValueError("characterization plan identity differs")
    artifact((parent/"acceptance-decision.json").as_posix(), evidence["acceptance_sha256"])
    circuit = artifact((parent/"circuit.scs").as_posix(), evidence["circuit_sha256"])
    if evidence["kind"] != "electrical-block-characterization" or evidence["dataset"] != "public_calibration" or evidence["source_recheck"] != "PASS":
        raise ValueError("unsupported characterization evidence")
    if evidence["source_generation"] != gate["outputs"]["source_generation"]:
        raise ValueError("source generation mismatch")
    if [c["id"] for c in plan["cases"]] != [c["id"] for c in evidence["cases"]]:
        raise ValueError("case coverage differs from plan")
    signals = {pin:"V" for pin in plan["target"]["term_order"]}
    signals["VPROBE:p"] = "A"
    for spec,case in zip(plan["cases"],evidence["cases"]):
        if case["case_digest"] != stable_digest(spec):
            raise ValueError("case digest differs from stimulus")
        files=[]
        for record in case["artifacts"]:
            path=artifact(record["path"],record["sha256"])
            if path.stat().st_size != record["size"]:
                raise ValueError("case artifact size mismatch")
            if path.name == "waveforms.csv": files.append(path)
        if len(files) != 1:
            raise ValueError("one waveform CSV per case is required")
        measured = measure_csv(files[0],signals,plan["analysis"]["sample_times_s"],plan["analysis"]["stop_s"])
        if measured != case["measurements"]:
            raise ValueError("measurement summary differs from source CSV")
    models=[]
    for record in evidence["model_files"]:
        artifact((parent/"models"/record["relative_path"]).as_posix(),record["sha256"])
        models.append({"relative_path":record["relative_path"],"sha256":record["sha256"]})
    request = json.loads((control_path.parent / "request.json").read_text())
    repeat = {"run_id":control["run_id"],"manifest_status":"PASS","execution_status":"PASS",
              "inputs":{"source_generation":evidence["source_generation"],"experiment_digest":evidence["experiment_digest"],
                        "acceptance_sha256":evidence["acceptance_sha256"],"circuit_sha256":evidence["circuit_sha256"],
                        "model_files":models,"tools":request["tools"],"tool_qualification":request["tool_qualification"]},
              "measurements":[{"id":c["id"],"measurements":c["measurements"],"product":c["product"]} for c in evidence["cases"]]}
    return {"control_path":str(control_path),"payload_root":str(root),"evidence":evidence,
            "experiment":plan,"circuit":str(circuit),"repeat_record":repeat}
