#!/usr/bin/env python3
"""Reserve evaluator-owned whole cases before the next public-data fit.

Only the commitment is public. This prepares data; same-UID filesystem
permissions alone are not a qualified production evaluator isolation boundary.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import secrets

from aivworkbench.electrical_experiment import validate_experiment
from aivworkbench.experiment_partition import reserve_holdout, stratified_points
from aivworkbench.manifest import publish_split_manifests, verify_split_manifests
from aivworkbench.trajectory_planning import change_ramp
from aivworkbench.workspace import (allocate_split_run, resolve_launch_paths,
                                   sha256_file, stable_digest, write_json_once)


def partition_case(plan, case):
    return {"kind": "electrical_dynamic_trajectory",
            "stimulus": {"target": plan["target"], "roles": plan["roles"], "model": plan["model"],
                         "voltages": case["voltages"], "load_ohm": case["load_ohm"],
                         "load_conductance_pwl_s": case["load_conductance_pwl_s"]},
            "measurement": {"analysis": plan["analysis"], "definition": plan["measurement"],
                            "events": [{k: v for k, v in e.items() if k != "id"} for e in case["events"]]}}


def reserve(plan_path, policy_path, *, sequences=False):
    plan = validate_experiment(json.loads(plan_path.read_text()))
    public = [partition_case(plan, c) for c in plan["cases"]]
    domain = {"experiment_sha256": sha256_file(plan_path), "ramp_duration_s": [2e-6, 40e-6],
              "event_start_s": [80e-6, 140e-6], "sampling": "two_boundary_plus_two_stratified_per_event_kind",
              "sequences": "independent_two_event_sequences" if sequences else "NOT_RESERVED",
              "pvt_and_amplitudes": "fixed_to_public_plan",
              "scope": "sequential_multirate_slice_only" if sequences else "single_event_multirate_slice_only"}
    seed = secrets.randbits(63)
    rows = stratified_points({"ramp": domain["ramp_duration_s"], "start": domain["event_start_s"]}, 2, seed=seed)
    rows += [{"ramp": 2e-6, "start": 80e-6}, {"ramp": 40e-6, "start": 140e-6}]
    hidden = []
    selected = [c for c in plan["cases"] if (len(c["events"]) == 2 if sequences else c["id"].endswith("_rate_0"))]
    if not selected:
        raise ValueError("no matching whole-case trajectories in plan")
    for base in selected:
        for index, row in enumerate(rows):
            case = deepcopy(base)
            for event_index, event in enumerate(case["events"]):
                start, end = event["start_s"], event["end_s"]
                offset = 0. if event_index == 0 else .0004
                new_start = offset+row["start"]
                duration = row["ramp"] if event_index == 0 else rows[(index+1)%len(rows)]["ramp"]
                event["start_s"], event["end_s"] = new_start, new_start+duration
                points = (case["load_conductance_pwl_s"] if event["signal"] == "load_conductance"
                          else case["voltages"][event["signal"]]["pwl"])
                for point in points:
                    if point[0] == start: point[0] = new_start
                    elif point[0] == end: point[0] = new_start+duration
            case["id"] = "reserved_%d" % len(hidden)
            check = deepcopy(plan)
            check["cases"] = [case]
            validate_experiment(check)
            hidden.append(partition_case(plan, case))
    request = {"kind": "dynamic-holdout-reservation", "domain": domain,
               "policy_sha256": sha256_file(policy_path), "candidate": "NOT_FROZEN",
               "evaluator_isolation": "NOT_QUALIFIED"}
    digest = stable_digest(request)
    paths = allocate_split_run(resolve_launch_paths(), Path("/path/to/xh/amsVerify/data/ams").resolve(),
        library=plan["target"]["library"], cell=plan["target"]["cell"], view="dynamic_evaluator",
        kind="dynamic-holdout-reservation", request_digest=digest)
    commitment = reserve_holdout(paths.payload_root/"private", public_cases=public, hidden_cases=hidden,
        seed=seed, domain_digest=stable_digest(domain), policy_digest=sha256_file(policy_path))
    summary = {**request, "commitment": commitment, "status": "RESERVED_NOT_EVALUATED",
               "qualification": "NOT_ESTABLISHED", "private_payload_indexed": False}
    write_json_once(paths.control_root/"request.json", request)
    write_json_once(paths.payload_root/"public-commitment.json", summary)
    publish_split_manifests(paths, request_digest=digest, status=summary["status"],
        control_details={"payload_root": str(paths.payload_root), **summary}, payload_details=summary,
        control_artifacts=[paths.control_root/"request.json"],
        payload_artifacts=[paths.payload_root/"public-commitment.json"])
    verify_split_manifests(paths.control_manifest, paths.payload_manifest)
    print(json.dumps({"run_id": paths.run_id, "control_manifest": str(paths.control_manifest),
                      "commitment": commitment, "status": summary["status"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("policy", type=Path)
    parser.add_argument("--sequences", action="store_true")
    args = parser.parse_args()
    reserve(args.plan, args.policy, sequences=args.sequences)
