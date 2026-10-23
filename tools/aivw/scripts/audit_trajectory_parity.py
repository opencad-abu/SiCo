#!/usr/bin/env python3
"""Authenticate public Xcelium waveforms and independently replay Python state.

This checks implementation parity only. It never evaluates private holdout or
claims transistor correlation, and cannot change the candidate verdict.
"""

import argparse
import json
from pathlib import Path

from aivworkbench.manifest import publish_split_manifests, resolve_indexed_artifact, verify_split_manifests
from aivworkbench.plugins.sampled_state import simulate
from aivworkbench.plugins.sampled_state_sv import render_model
from aivworkbench.waveform_metrics import read_waveform
from aivworkbench.workspace import allocate_split_run, resolve_launch_paths, sha256_file, stable_digest, write_json_once


def audit(control_path):
    control = json.loads(control_path.read_text())
    root = Path(control["details"]["payload_root"])
    pair = verify_split_manifests(control_path, root/"payload-manifest.json")
    def read(relative):
        return resolve_indexed_artifact(control_path, relative).path
    model = json.loads(read("checks/gates/model/model.json").read_text())
    candidate = read("checks/gates/model/candidate.sv")
    plan = json.loads(read("checks/gates/public_data/experiment.json").read_text())
    rnm = json.loads(read("checks/gates/rnm/trajectory-evidence.json").read_text())
    evidence = json.loads(read("checks/gates/public_data/characterization-evidence.json").read_text())
    identity = dict(model)
    digest = identity.pop("model_digest")
    if (stable_digest(identity) != digest or render_model(model) != candidate.read_text() or
            rnm["candidate_sha256"] != sha256_file(candidate) or
            evidence["dataset"] != "public_calibration" or
            model["experiment_digest"] != stable_digest(plan) or
            rnm["experiment_digest"] != stable_digest(plan) or
            model["source_generation"] != evidence["source_generation"] or
            rnm["source_generation"] != evidence["source_generation"] or
            rnm["status"] != "EXECUTED" or
            [c["id"] for c in plan["cases"]] != [c["id"] for c in rnm["cases"]]):
        raise ValueError("public candidate/experiment identity mismatch")
    checks = []
    for case, record in zip(plan["cases"], rnm["cases"]):
        csv = read(record["csv"])
        if record["case_digest"] != stable_digest(case) or record["sha256"] != sha256_file(csv):
            raise ValueError("RNM stimulus or waveform hash differs")
        signals = {plan["roles"]["output"]: "V", "VPROBE:p": "A"}
        actual = read_waveform(csv, signals, plan["analysis"]["stop_s"])
        expected = simulate(model, plan, case)
        if actual.times != expected.times:
            raise ValueError("Python/SV physical sample axes differ")
        errors = {name: max(abs(a-b) for a, b in zip(actual.values[name], expected.values[name])) for name in signals}
        # Numerical renderer limits, explicitly separate from product/RNM policy.
        passed = errors[plan["roles"]["output"]] <= 1e-10 and errors["VPROBE:p"] <= 1e-12
        checks.append({"case": case["id"], "status": "PASS" if passed else "FAIL",
                       "samples": len(actual.times), "max_absolute_error": errors})
        print(json.dumps(checks[-1]), flush=True)
    verify_split_manifests(control_path, root/"payload-manifest.json")
    request = {"kind": "sampled-model-implementation-parity", "source_pair": pair,
               "script_sha256": sha256_file(Path(__file__)), "candidate_sha256": sha256_file(candidate)}
    request_digest = stable_digest(request)
    run = allocate_split_run(resolve_launch_paths(), Path("/path/to/xh/amsVerify/data/ams").resolve(),
        library="aivw", cell=model["module"], view="implementation_parity", kind="trajectory-parity", request_digest=request_digest)
    summary = {"schema_version": 1, **request, "status": "PASS_IMPLEMENTATION_PARITY" if all(c["status"] == "PASS" for c in checks) else "FAIL_IMPLEMENTATION_PARITY",
               "checks": checks, "qualification": "NOT_ESTABLISHED", "holdout_evaluated": False}
    write_json_once(run.control_root/"request.json", request)
    write_json_once(run.payload_root/"summary.json", summary)
    publish_split_manifests(run, request_digest=request_digest, status=summary["status"],
        control_details={"payload_root": str(run.payload_root)}, payload_details={"scope": "implementation_parity_only"},
        control_artifacts=[run.control_root/"request.json"], payload_artifacts=[run.payload_root/"summary.json"])
    verify_split_manifests(run.control_manifest, run.payload_manifest)
    print(json.dumps({"run_id": run.run_id, "status": summary["status"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    audit(parser.parse_args().manifest)
