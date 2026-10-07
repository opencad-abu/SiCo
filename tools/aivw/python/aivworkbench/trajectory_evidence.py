"""Authenticate public trajectory producers before modeling or comparison."""

from __future__ import annotations

import json
from pathlib import Path

from .electrical_experiment import validate_experiment
from .workspace import sha256_file, stable_digest
from .waveform_metrics import read_waveform


def indexed_file(context, dependency, reference, digest=None):
    relative = Path(reference)
    root = context.run.payload_root.resolve()
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe trajectory artifact reference")
    candidate = root/relative
    if any((root/Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts)+1)):
        raise ValueError("trajectory artifact traverses a symlink")
    if not candidate.is_file() or candidate not in {Path(p).resolve() for p in dependency.artifacts}:
        raise ValueError("trajectory artifact not indexed by its producer")
    if digest is not None and sha256_file(candidate) != digest:
        raise ValueError("trajectory artifact digest differs")
    return candidate


def public_trajectories(context):
    producers = [d for d in context.dependencies.values() if "characterization_evidence" in d.outputs]
    if len(producers) != 1 or producers[0].status != "PASS":
        raise ValueError("one successful public characterization producer required")
    producer = producers[0]
    path = indexed_file(context, producer, producer.outputs["characterization_evidence"],
                        producer.outputs["characterization_evidence_sha256"])
    evidence = json.loads(path.read_text())
    plan_path = indexed_file(context, producer, str((path.parent/"experiment.json").relative_to(context.run.payload_root)),
                             evidence["experiment_sha256"])
    plan = validate_experiment(json.loads(plan_path.read_text()))
    if plan["schema_version"] != 2 or stable_digest(plan) != evidence["experiment_digest"]:
        raise ValueError("dynamic experiment identity differs")
    if evidence["kind"] != "electrical-block-characterization" or evidence["source_recheck"] != "PASS":
        raise ValueError("unsupported trajectory evidence")
    if plan["target"] != evidence["target"]:
        raise ValueError("trajectory target identity differs")
    if evidence["dataset"] != "public_calibration" or evidence["source_generation"] != producer.outputs["source_generation"]:
        raise ValueError("trajectory source generation or visibility differs")
    if [c["id"] for c in evidence["cases"]] != [c["id"] for c in plan["cases"]]:
        raise ValueError("trajectory case coverage differs")
    signals = {pin: "V" for pin in plan["target"]["term_order"]}
    signals["VPROBE:p"] = "A"
    waves = {}
    for case, record in zip(plan["cases"], evidence["cases"]):
        if stable_digest(case) != record["case_digest"]:
            raise ValueError("trajectory stimulus differs from producer")
        artifacts = record["artifacts"]
        for item in artifacts:
            file = indexed_file(context, producer, item["path"], item["sha256"])
            if file.stat().st_size != item["size"]:
                raise ValueError("trajectory artifact size differs")
        csvs = [r for r in artifacts if r["path"].endswith("/waveforms.csv")]
        if len(csvs) != 1:
            raise ValueError("one source waveform required per trajectory")
        waves[case["id"]] = read_waveform(context.run.payload_root/csvs[0]["path"], signals, plan["analysis"]["stop_s"])
    return plan, evidence, waves
