#!/usr/bin/env python3
"""Run two identical frozen candidate repeats on one reserved hidden scope.

Public orchestration only: verifies the boundary qualification and the
candidate freeze, stages public inputs, then executes the hidden evaluation
inside the boundary.  Aggregate results are published; per-case waveforms
stay in the private root.
"""

import argparse
import json
from pathlib import Path
import shutil
import sys

from aivworkbench.manifest import publish_split_manifests, resolve_indexed_artifact, verify_split_manifests
from aivworkbench.qualification.candidate_freeze import _pair
from aivworkbench.qualification.dynamic_admission import _json
from aivworkbench.profiles import load_profile
from aivworkbench.toolchain import capture_module_environment, probe_tools
from aivworkbench.qualification.private_boundary import run_boundary
from aivworkbench.workspace import allocate_split_run, resolve_launch_paths, sha256_file, stable_digest, write_json_once


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reservation", type=Path, required=True, help="hidden-scope reservation control")
    parser.add_argument("--boundary", type=Path, required=True, help="boundary qualification control")
    parser.add_argument("--freeze", type=Path, required=True, help="candidate freeze control")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True, help="public skeleton experiment plan")
    parser.add_argument("--timeout", type=float, default=600.0, help="per-attempt Xcelium deadline")
    args = parser.parse_args()
    from aivworkbench.qualification.private_boundary import verify_boundary_report
    boundary = verify_boundary_report(args.boundary, args.reservation)
    freeze = _json(args.freeze, "candidate-freeze.json")
    policy_sha = sha256_file(args.policy)
    if freeze["policy_sha256"] != policy_sha:
        raise SystemExit("freeze policy differs from --policy")
    reservation_pair = _pair(args.reservation)
    if reservation_pair["run_id"] not in {pair["run_id"] for pair in freeze["reservations"].values()}:
        raise SystemExit("reservation is not bound to the freeze record")
    commitment = _json(args.reservation, "public-commitment.json")["commitment"]
    source_control = Path(freeze["sources"]["source"]["control_manifest"])
    candidate_source = resolve_indexed_artifact(source_control, "xcelium/candidate.sv").path
    model_path = resolve_indexed_artifact(source_control, "model.json").path
    if sha256_file(candidate_source) != freeze["candidate_sha256"]:
        raise SystemExit("frozen candidate bytes differ from the freeze record")
    profile = load_profile("amsverify")
    environment = capture_module_environment(profile.modules)
    qualified = probe_tools([tool for tool in profile.tools
                             if tool.name in ("xrun", "spectre", "ocean")], environment)
    tools = {tool.name: tool for tool in qualified if tool.status == "PASS"}
    if set(tools) != {"xrun", "spectre", "ocean"}:
        raise SystemExit("Xcelium/Spectre/OCEAN qualification unavailable")
    reference_control = Path(freeze["sources"]["reference"]["control_manifest"])
    reference_pair = _pair(reference_control)
    reference_root = Path(json.loads(reference_control.read_text())["details"]["payload_root"])
    schedule_plugin = Path(__file__).resolve().parents[1]/"python/aivworkbench/plugins/transition_response.py"
    request = {"kind": "dynamic-holdout-candidate-repeats", "candidate_sha256": freeze["candidate_sha256"],
               "policy_sha256": policy_sha, "commitment_sha256": commitment["commitment_sha256"],
               "domain_digest": commitment["domain_digest"], "boundary": boundary["boundary"],
               "reservation": reservation_pair, "reference": reference_pair,
               "tools": {name: {"path": tool.path, "sha256": sha256_file(Path(tool.path))}
                         for name, tool in sorted(tools.items())},
               "plan_sha256": sha256_file(args.plan),
               "implementation": {
                   str(Path(__file__).resolve()): sha256_file(Path(__file__)),
                   str(schedule_plugin): sha256_file(schedule_plugin)}}
    digest = stable_digest(request)
    run = allocate_split_run(resolve_launch_paths(), Path("/path/to/xh/amsVerify/data/ams").resolve(),
        library=freeze["target"]["library"], cell=freeze["target"]["cell"], view="dynamic_evaluator",
        kind="dynamic-holdout-candidate-repeats", request_digest=digest)
    spool = run.payload_root/"staging"
    spool.mkdir(parents=True)
    staged_source = spool/"candidate.sv"
    staged_model = spool/"model.json"
    staged_plan = spool/"skeleton-plan.json"
    staged_env = spool/"environment.json"
    staged_policy = spool/"policy.json"
    staged_circuit = spool/"circuit.scs"
    staged_models = spool/"models"
    staged_cdslib = spool/"cds.lib"
    shutil.copyfile(candidate_source, staged_source)
    shutil.copyfile(model_path, staged_model)
    shutil.copyfile(args.plan, staged_plan)
    shutil.copyfile(args.policy, staged_policy)
    shutil.copyfile(reference_root/"checks/gates/characterization/circuit.scs", staged_circuit)
    shutil.copytree(reference_root/"checks/gates/characterization/models", staged_models)
    staged_cdslib.write_text("# hidden evaluation cds lib\n")
    write_json_once(staged_env, environment)
    private_root = Path(json.loads(args.reservation.read_text())["details"]["payload_root"])/"private"
    contract_digest = stable_digest({"policy_sha256": policy_sha, "plan_sha256": request["plan_sha256"],
                                     "candidate_sha256": freeze["candidate_sha256"]})
    config = {"private_root": str(private_root), "spool": str(spool),
              "candidate_source": str(staged_source), "model": str(staged_model),
              "skeleton_plan": str(staged_plan), "environment": str(staged_env),
              "policy": str(staged_policy), "circuit": str(staged_circuit),
              "models": str(staged_models), "cdslib": str(staged_cdslib),
              "reference_run_id": reference_pair["run_id"],
              "xrun": tools["xrun"].path, "spectre": tools["spectre"].path, "ocean": tools["ocean"].path,
              "timeout": args.timeout, "run_id": run.run_id,
              "renderer": "transition_response", "candidate_sha256": freeze["candidate_sha256"],
              "contract_digest": contract_digest, "commitment_sha256": commitment["commitment_sha256"],
              "policy_digest": commitment["policy_digest"], "domain_digest": commitment["domain_digest"],
              "case_count": commitment["case_count"]}
    config_path = spool/"config.json"
    write_json_once(config_path, config)
    aivw_python = Path(__file__).resolve().parents[1]/"python"
    common_python = aivw_python.parents[1]/"common/python"
    summary = {"schema_version": 1, "kind": "dynamic-holdout-candidate-repeats",
               "status": "BLOCKED_BOUNDARY", "qualification": "NOT_ESTABLISHED",
               "case_count": commitment["case_count"], "repeat": {"status": "BLOCKED_REPEAT"}}
    try:
        completed = run_boundary(
            ["/software/pkgs/python/3.9.13/bin/python3", "-s", "-m", "aivworkbench.qualification.hidden_evaluation"],
            spool=spool,
            environment={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
                         "PYTHONNOUSERSITE": "1",
                         "HOME": str(spool), "TMPDIR": "/tmp",
                         "PYTHONPATH": "%s:%s" % (aivw_python, common_python),
                         "HIDDEN_CONFIG": str(config_path)},
            timeout=args.timeout*2 + 600.0,
            extra_mounts=((private_root, str(private_root), True),
                          (aivw_python, str(aivw_python), False),
                          (common_python, str(common_python), False)))
        parsed = None
        for line in reversed(completed.stdout.splitlines()):
            if line.strip().startswith("{"):
                parsed = json.loads(line.strip())
                break
        if parsed is None:
            raise ValueError("boundary produced no summary")
        summary = parsed
    except Exception as exc:  # noqa: BLE001 - published as blocked evidence
        summary = {**summary, "error": "%s: %s" % (type(exc).__name__, exc)}
    write_json_once(run.control_root/"request.json", request)
    summary["boundary_kind"] = boundary["boundary"]
    summary["boundary_report"] = {"run_id": boundary["reservation"]["run_id"],
                                  "commitment_sha256": boundary["reservation"]["commitment_sha256"]}
    write_json_once(run.payload_root/"summary.json", summary)
    publish_split_manifests(run, request_digest=digest, status=summary["status"],
        control_details={"payload_root": str(run.payload_root), "status": summary["status"]},
        payload_details={"scope": "two_identical_frozen_candidate_repeats_on_reserved_hidden_cases"},
        control_artifacts=[run.control_root/"request.json"],
        payload_artifacts=[path for path in run.payload_root.rglob("*") if path.is_file()])
    pair = verify_split_manifests(run.control_manifest, run.payload_manifest)
    print(json.dumps({"run_id": run.run_id, "status": summary["status"], "integrity": pair["status"],
                      "case_count": summary["case_count"], "repeat": summary["repeat"]["status"]}))
    return 0 if summary.get("status") == "HIDDEN_CANDIDATE_REPEATS_PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
