#!/usr/bin/env python3
"""Bind repeat, isolated revision, and network evidence without promoting it."""

import json
from pathlib import Path

from aivworkbench.acceptance import compare_repeats
from aivworkbench.characterization_evidence import read_characterization
from aivworkbench.manifest import publish_split_manifests, verify_split_manifests
from aivworkbench.trajectory_readiness import assess_sampled_candidate
from aivworkbench.workspace import allocate_split_run, resolve_launch_paths, sha256_file, stable_digest, write_json_once


TARGETS = {
    "MASTER": {"source": ["20260907T234715.623528Z-recipe-run-22c11634b854", "20260908T002322.335868Z-recipe-run-22c11634b854"],
               "revision": "20260908T002206.919677Z-trajectory-revision-audit-09019b846ac5",
               "adapter": "20260908T002238.344535Z-eenet-adapter-e1db36a9ae76",
               "v5": "20260908T001050.535894Z-recipe-run-68023720372b",
               "v6": "20260908T001838.659739Z-recipe-run-d98265d71c14",
               "v7": "20260908T004841.240166Z-recipe-run-73b6205911d0",
               "sequence_reservation": "20260908T002653.134041Z-dynamic-holdout-reservation-18adc08f1ac6"},
    "AON": {"source": ["20260907T235333.535070Z-recipe-run-279edfe375e7", "20260908T002322.335844Z-recipe-run-279edfe375e7"],
            "revision": "20260908T002655.502800Z-trajectory-revision-audit-5ffafe0c4266",
            "adapter": "20260908T002238.707861Z-eenet-adapter-c5d97ed9d07b",
            "v5": "20260908T001050.535838Z-recipe-run-3c4e956b0861",
            "v6": "20260908T002018.129961Z-recipe-run-658ff7e7d809",
            "v7": "20260908T005123.530164Z-recipe-run-e8274027599c",
            "sequence_reservation": "20260908T002653.287716Z-dynamic-holdout-reservation-ac771871fe91"},
}


def main():
    launch = resolve_launch_paths()
    index = []

    def read_run(rid):
        control = launch.physical_artifact_root/"runs"/rid/"manifest.json"
        value = json.loads(control.read_text())
        root = Path(value["details"]["payload_root"])
        pair = verify_split_manifests(control, root/"payload-manifest.json")
        index.append({"run_id": rid, "control_manifest": str(control), "sha256": sha256_file(control),
                      "integrity": pair["status"], "run_status": pair["run_status"]})
        return control, root, value

    targets = {}
    for cell, refs in TARGETS.items():
        sources = [read_characterization(read_run(rid)[0]) for rid in refs["source"]]
        repeats = compare_repeats([s["repeat_record"] for s in sources])
        if repeats["status"] != "PASS":
            raise ValueError("source repeat is not reproducible")
        # Compare complete CSV content in addition to normalized sampled values.
        csv_hashes = []
        for source in sources:
            csv_hashes.append({c["id"]: next(a["sha256"] for a in c["artifacts"] if a["path"].endswith("/waveforms.csv"))
                               for c in source["evidence"]["cases"]})
        if csv_hashes[0] != csv_hashes[1]:
            raise ValueError("complete source waveform repeat differs")
        revision_control, revision_root, revision_value = read_run(refs["revision"])
        isolation = json.loads((revision_root/"provider-isolation.json").read_text())
        if (revision_value["details"]["protocol_check"] != "PASS" or len(isolation) != 3 or
                any(x["network"] != "isolated" or x["host_project_mounted"] for x in isolation)):
            raise ValueError("isolated provider execution evidence is incomplete")
        correlation = json.loads((revision_root/"agent/revision_001/correlation.json").read_text())
        revision = json.loads((revision_root/"agent/revision-workflow.json").read_text())
        model = json.loads((revision_root/"agent/evaluation-inputs.json").read_text())["model"]
        adapter_control, _, _ = read_run(refs["adapter"])
        readiness = assess_sampled_candidate(model, correlation, revision, adapter_manifest=adapter_control)
        versions = {}
        for version in ("v5", "v6", "v7"):
            _, root, _ = read_run(refs[version])
            report = json.loads((root/"checks/gates/correlation/correlation.json").read_text())
            versions[version] = {"candidate_sha256": report["candidate_sha256"], "status": report["status"],
                "diagnostic_status": report["diagnostic_status"], "case_count": len(report["cases"]),
                "passed_cases": sum(c["diagnostic_status"] == "PASS" for c in report["cases"]),
                "failed_cases": [c["id"] for c in report["cases"] if c["diagnostic_status"] != "PASS"]}
        _, reservation_root, _ = read_run(refs["sequence_reservation"])
        reservation = json.loads((reservation_root/"public-commitment.json").read_text())
        targets[cell] = {"source_repeat": repeats, "full_csv_repeat_identical": csv_hashes[0] == csv_hashes[1],
            "source_generation": sources[-1]["evidence"]["source_generation"],
            "source_before_after_recheck": sources[-1]["evidence"]["source_recheck"],
            "isolated_revision_protocol": "PASS", "system_readiness": readiness, "models": versions,
            "sequence_holdout": reservation["commitment"], "sequence_reservation_applies_to": "future_candidate_only"}
    summary = {"schema_version": 1, "kind": "dynamic-todo-evidence-audit", "status": "PASS_EVIDENCE_AUDIT",
        "qualification": "NOT_ESTABLISHED", "targets": targets, "runs": index,
        "policy": "proposed_not_approved", "final_holdout_evaluated": False,
        "actual_model_service_qualification": "NOT_PERFORMED", "full_ams_system_simulation": "NOT_PERFORMED",
        "publication": "NOT_PERFORMED"}
    request = {"kind": summary["kind"], "input_manifests": index, "audit_script_sha256": sha256_file(Path(__file__))}
    digest = stable_digest(request)
    run = allocate_split_run(launch, Path("/path/to/xh/amsVerify/data/ams").resolve(),
        library="aivw", cell="dynamic_checkpoint", view="evidence", kind="dynamic-todo-audit", request_digest=digest)
    write_json_once(run.control_root/"request.json", request)
    write_json_once(run.payload_root/"summary.json", summary)
    publish_split_manifests(run, request_digest=digest, status=summary["status"],
        control_details={"payload_root": str(run.payload_root), "qualification": "NOT_ESTABLISHED"},
        payload_details={"scope": "evidence_audit_not_behavior_qualification"},
        control_artifacts=[run.control_root/"request.json"], payload_artifacts=[run.payload_root/"summary.json"])
    pair = verify_split_manifests(run.control_manifest, run.payload_manifest)
    print(json.dumps({"run_id": run.run_id, "summary": str(run.payload_root/"summary.json"),
                      "integrity": pair["status"], "full_csv_repeat_identical": {k:v["full_csv_repeat_identical"] for k,v in targets.items()}}))


if __name__ == "__main__":
    main()
