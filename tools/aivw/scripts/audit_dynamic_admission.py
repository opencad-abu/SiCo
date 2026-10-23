#!/usr/bin/env python3
"""Audit public holdout prerequisites without opening or freezing private data."""

import argparse
import json
from pathlib import Path

from aivworkbench.manifest import publish_split_manifests, verify_split_manifests
from aivworkbench.qualification.dynamic_admission import audit_admission, audit_frozen_admission
from aivworkbench.workspace import allocate_split_run, resolve_launch_paths, sha256_file, stable_digest, write_json_once


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--single-event", type=Path, required=True)
    parser.add_argument("--sequence", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--frozen", action="store_true",
                        help="candidate is a frozen refined-matrix run of the recalibrated candidate")
    parser.add_argument("--reference", type=Path, help="primary reference characterization run (frozen mode)")
    parser.add_argument("--crosscheck", type=Path, help="crosscheck reference characterization run (frozen mode)")
    parser.add_argument("--source", type=Path, help="frozen candidate source run (frozen mode)")
    parser.add_argument("--freeze", type=Path, help="candidate-freeze run to verify (frozen mode)")
    args = parser.parse_args()
    if args.frozen:
        if not all((args.reference, args.crosscheck, args.source)):
            parser.error("--frozen requires --reference/--crosscheck/--source")
        summary = audit_frozen_admission(args.candidate, args.reference, args.crosscheck, args.source,
            args.single_event, args.sequence, args.policy, args.experiment, args.freeze)
    else:
        summary = audit_admission(args.candidate, args.single_event, args.sequence, args.policy, args.experiment)
    request = {"kind": summary["kind"], "candidate": summary["candidate"]["reference"],
               "reservations": {k: v["reference"] for k, v in summary["reservations"].items()},
               "policy_sha256": summary["candidate"]["policy_sha256"],
               "experiment_sha256": summary["candidate"]["plan_file_sha256"],
               "script_sha256": sha256_file(Path(__file__)),
               "audit_module_sha256": sha256_file(Path(__file__).resolve().parents[1]/"python/aivworkbench/qualification/dynamic_admission.py")}
    digest = stable_digest(request)
    target = summary["candidate"]["target"]
    run = allocate_split_run(resolve_launch_paths(), Path("/path/to/xh/amsVerify/data/ams").resolve(),
        library=target["library"], cell=target["cell"], view="dynamic_admission",
        kind="dynamic-admission-audit", request_digest=digest)
    write_json_once(run.control_root/"request.json", request)
    write_json_once(run.payload_root/"summary.json", summary)
    publish_split_manifests(run, request_digest=digest, status=summary["status"],
        control_details={"payload_root": str(run.payload_root), "qualification": "NOT_ESTABLISHED"},
        payload_details={"scope": "public_preflight_no_private_access"},
        control_artifacts=[run.control_root/"request.json"], payload_artifacts=[run.payload_root/"summary.json"])
    pair = verify_split_manifests(run.control_manifest, run.payload_manifest)
    print(json.dumps({"run_id": run.run_id, "integrity": pair["status"], "status": summary["status"],
        "disposition": summary["disposition"], "blockers": summary["blockers"], "summary": str(run.payload_root/"summary.json")}))


if __name__ == "__main__":
    main()
