#!/usr/bin/env python3
"""Quantify reference-to-reference spread inside the absolute-floor windows.

Public data only; no EDA execution.  The report tells the acceptance
decision how much of the proposed absolute floor is numerical reference
spread in the windows where the floor is the binding tolerance.
"""

import argparse
import json
from pathlib import Path

from correlate_refined_public_matrix import bind, projection, reference
from aivworkbench.floor_sensitivity import case_spread
from aivworkbench.manifest import publish_split_manifests, verify_split_manifests
from aivworkbench.workspace import (allocate_split_run, resolve_launch_paths,
                                    sha256_file, stable_digest, write_json_once)


def _pick(rows, kind, field, time_field):
    row = max(rows, key=lambda item: item[kind][field])
    return {field: row[kind][field], "case": row["id"], "time_s": row[kind][time_field]}


def analyze(config_path, policy_path, targets):
    config = json.loads(config_path.read_text())
    policy = json.loads(policy_path.read_text())
    report = {"kind": "absolute-floor-reference-spread", "schema_version": 1,
              "policy_sha256": sha256_file(policy_path), "holdout_evaluated": False,
              "qualification": "NOT_ESTABLISHED", "targets": {}}
    pairs = []
    target_identity = None
    for target in targets:
        spec = config["targets"][target]
        ref_pair, ref_path = bind(spec["reference"])
        cross_pair, cross_path = bind(spec["crosscheck"])
        pairs += [ref_pair, cross_pair]
        plan, _evidence, waves, _logs = reference(ref_path)
        other_plan, _other_evidence, other_waves, _other_logs = reference(cross_path)
        if projection(plan) != projection(other_plan):
            raise ValueError("physical public experiment differs across references")
        rows = [{"id": case["id"], **case_spread(waves[case["id"]], other_waves[case["id"]], policy,
                                                 output=plan["roles"]["output"], current="VPROBE:p")}
                for case in plan["cases"]]
        report["targets"][target] = {
            "reference": spec["reference"], "crosscheck": spec["crosscheck"],
            "cells": {"library": plan["target"]["library"], "cell": plan["target"]["cell"]},
            "cases": rows,
            "worst_reference_spread": {
                "voltage": _pick(rows, "voltage", "max_reference_spread", "max_spread_time_s"),
                "current": _pick(rows, "current", "max_reference_spread", "max_spread_time_s"),
            },
            "worst_binding_window_spread": {
                "voltage": _pick(rows, "voltage", "binding_window_spread", "binding_window_spread_time_s"),
                "current": _pick(rows, "current", "binding_window_spread", "binding_window_spread_time_s"),
            },
            "binding_samples": {
                "voltage": sum(r["voltage"]["floor_binding_samples"] for r in rows),
                "current": sum(r["current"]["floor_binding_samples"] for r in rows),
            },
        }
        target_identity = {"library": plan["target"]["library"], "cell": plan["target"]["cell"]}
    return report, pairs, target_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("recipes/ldo/refined_public_matrix_v2.json"))
    parser.add_argument("--policy", type=Path, default=Path("recipes/ldo/dynamic_acceptance_proposal_v1.json"))
    parser.add_argument("--target", action="append", dest="targets")
    parser.add_argument("--payload-root", type=Path,
                        default=Path("/path/to/xh/amsVerify/data/ams"))
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    targets = args.targets or sorted(config["targets"])
    report, pairs, target = analyze(args.config, args.policy, targets)
    request = {"kind": report["kind"], "config_sha256": sha256_file(args.config),
               "policy_sha256": report["policy_sha256"], "targets": targets,
               "sources": pairs,
               "implementation": {str(Path(__file__).resolve()): sha256_file(Path(__file__))}}
    digest = stable_digest(request)
    run = allocate_split_run(resolve_launch_paths(), args.payload_root.resolve(),
        library=target["library"], cell=target["cell"], view="floor_sensitivity",
        kind="floor-sensitivity-analysis", request_digest=digest)
    write_json_once(run.control_root/"request.json", request)
    write_json_once(run.payload_root/"summary.json", report)
    for pair in pairs:
        if verify_split_manifests(Path(pair["control_manifest"]), Path(pair["payload_manifest"])) != pair:
            raise ValueError("reference manifest changed during analysis")
    publish_split_manifests(run, request_digest=digest, status="ANALYZED_EVIDENCE",
        control_details={"payload_root": str(run.payload_root), "targets": targets},
        payload_details={"scope": "public_reference_spread_in_floor_windows"},
        control_artifacts=[run.control_root/"request.json"],
        payload_artifacts=[run.payload_root/"summary.json"])
    pair = verify_split_manifests(run.control_manifest, run.payload_manifest)
    print(json.dumps({"run_id": run.run_id, "integrity": pair["status"],
                      "worst_binding_window_spread": {t: report["targets"][t]["worst_binding_window_spread"] for t in targets},
                      "summary": str(run.payload_root/"summary.json")}))


if __name__ == "__main__":
    main()
