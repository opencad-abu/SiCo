#!/usr/bin/env python3
"""Replay two authenticated public candidates through real behavior gates.

Provider-free sequential replay: the script drives the deterministic
TrajectoryGateEvaluator directly (no agent protocol, no model provider).
It does not claim autonomous AI generation or system qualification.
Run from the cad launch directory with the qualified Python environment.
"""

import argparse
import json
from pathlib import Path

from aivworkbench.executor import ExecutorContext
from aivworkbench.executors.characterization_import import run_characterization_import
from aivworkbench.manifest import publish_split_manifests, resolve_indexed_artifact, verify_split_manifests
from aivworkbench.plugins.sampled_state_sv import render_testbench
from aivworkbench.profiles import load_profile
from aivworkbench.recipe import load_builtin_recipe
from aivworkbench.toolchain import capture_module_environment, probe_tools
from aivworkbench.trajectory_evaluator import TrajectoryGateEvaluator, evaluate_candidate_sequence
from aivworkbench.trajectory_evidence import public_trajectories
from aivworkbench.trajectory_readiness import assess_sampled_candidate
from aivworkbench.workspace import (allocate_split_run, resolve_launch_paths,
                                   sha256_file, stable_digest, write_json_once)


def candidate_from_run(control_path):
    control = json.loads(control_path.read_text())
    root = Path(control["details"]["payload_root"])
    pair = verify_split_manifests(control_path, root/"payload-manifest.json")
    gates = [g for g in control["details"]["gate_results"].values()
             if g["executor"] == "electrical.identify_dynamics"]
    if len(gates) != 1 or gates[0]["status"] != "PASS":
        raise ValueError("one successfully generated public candidate is required")
    output = gates[0]["outputs"]
    source = resolve_indexed_artifact(control_path, output["model_source"]).path
    model_path = resolve_indexed_artifact(control_path, output["dynamic_model"]).path
    if (sha256_file(source) != output["model_source_sha256"] or
            sha256_file(model_path) != output["dynamic_model_sha256"]):
        raise ValueError("candidate producer identity differs")
    model = json.loads(model_path.read_text())
    identity = dict(model)
    digest = identity.pop("model_digest")
    if stable_digest(identity) != digest or model["dataset"] != "public_calibration":
        raise ValueError("invalid public candidate model identity")
    candidate = {"module": model["module"], "source": source.read_text(),
                 "source_generation": model["source_generation"], "model_version": model["version"]}
    return model, candidate, pair


def run(args):
    recipe = load_builtin_recipe(args.recipe or "ldo."+args.cell+"_dynamic_model_v3")
    sources = [candidate_from_run(Path(p).resolve()) for p in args.candidate_manifest]
    model = sources[-1][0]
    if any((m["module"], m["source_generation"], m["experiment_digest"], m["step_s"]) !=
           (model["module"], model["source_generation"], model["experiment_digest"], model["step_s"])
           for m, _, _ in sources):
        raise ValueError("replayed candidates have incompatible evaluation inputs")
    profile = load_profile("amsverify")
    env = capture_module_environment(profile.modules)
    tool_results = probe_tools([t for t in profile.tools if t.name == "xrun"], env)
    if len(tool_results) != 1 or tool_results[0].status != "PASS":
        raise ValueError("Xcelium qualification failed")
    tools = {t.name: t.path for t in tool_results}
    request = {"kind": "controlled-public-trajectory-revision", "recipe": recipe.recipe_id,
               "sources": [p for _, _, p in sources], "timeout": args.timeout,
               "driver": "provider_free_sequential_replay",
               "ai_generation": "NOT_PERFORMED", "hidden_holdout": "NOT_ACCESSED",
               "external_provider_qualification": "NOT_IN_SCOPE"}
    digest = stable_digest(request)
    run_paths = allocate_split_run(resolve_launch_paths(),
        Path("/path/to/xh/amsVerify/data/ams").resolve(), library=recipe.target["library"],
        cell=recipe.target["cell"], view="rnm_candidate", kind="trajectory-revision-audit", request_digest=digest)
    write_json_once(run_paths.control_root/"request.json", request)
    write_json_once(run_paths.payload_root/"tools.json", [t.to_dict() for t in tool_results])
    print(json.dumps({"run_id": run_paths.run_id, "payload": str(run_paths.payload_root)}), flush=True)
    summary = {"status": "FAIL_AUDIT", "sequence_check": "NOT_ESTABLISHED"}
    try:
        context = ExecutorContext(recipe, run_paths, "public_data", "evidence.import_characterization", {}, {}, {}, args.timeout)
        context.gate_root.mkdir(parents=True)
        imported = run_characterization_import(context)
        if imported.status != "PASS":
            raise ValueError(str(imported.summary))
        context = ExecutorContext(recipe, run_paths, "evaluation", "audit", {"public": imported}, {}, {}, args.timeout)
        plan, evidence, golden = public_trajectories(context)
        policy = json.loads(recipe.input_path("dynamic_acceptance").read_text())
        evaluator = TrajectoryGateEvaluator(run_paths.payload_root/"evaluation", plan=plan, evidence=evidence,
            golden=golden, model=model, policy=policy, environment=env, tools=tools,
            renderer=render_testbench, timeout=args.timeout)
        report = evaluate_candidate_sequence(evaluator, [c for _, c, _ in sources])
        write_json_once(run_paths.payload_root/"revision-report.json", report)
        revisions = report["revisions"]
        if len(revisions) != len(sources) or len(evaluator.reports) != len(sources):
            raise ValueError("candidate sequence did not reach every real behavior gate")
        for i, revision in enumerate(revisions):
            feedback = revision["gate_feedback"]
            if feedback["code"] != "physical_trajectory_correlation":
                raise ValueError("revision did not complete real trajectory correlation")
            if (revision["candidate_sha256"] != stable_digest(sources[i][1]) or
                    revision["gate_feedback_sha256"] != feedback["sha256"] or
                    revision["parent_sha256"] != (None if not i else revisions[i-1]["candidate_sha256"]) or
                    revision["feedback_sha256"] != (None if not i else revisions[i-1]["gate_feedback_sha256"]) or
                    feedback["evidence"].get("report_digest") != stable_digest(evaluator.reports[i]) or
                    feedback["evidence"].get("source_generation") != evidence["source_generation"]):
                raise ValueError("revision/parent/feedback identity mismatch")
        readiness = assess_sampled_candidate(model, evaluator.reports[-1], report)
        write_json_once(run_paths.payload_root/"system-readiness.json", readiness)
        # Recheck prior immutable inputs after EDA execution.
        for path in args.candidate_manifest:
            candidate_from_run(Path(path).resolve())
        summary = {"status": "BLOCKED_DYNAMIC_QUALIFICATION", "sequence_check": "PASS",
                   "scope": "provider_free_real_xcelium_public_spectre_feedback",
                   "ai_generation": "NOT_PERFORMED", "external_provider_qualification": "NOT_IN_SCOPE",
                   "driver": "provider_free_sequential_replay",
                   "revision_count": len(revisions), "cases_per_revision": len(plan["cases"]),
                   "system_status": readiness["status"],
                   "correlation": [{"status": r["status"], "diagnostic_status": r["diagnostic_status"],
                       "failed_cases": [c["id"] for c in r["cases"] if c["diagnostic_status"] != "PASS"],
                       "candidate_sha256": r["candidate_sha256"]} for r in evaluator.reports],
                   "live_source_recheck": "NOT_PERFORMED", "qualification": "NOT_ESTABLISHED"}
    except Exception as exc:
        summary["reason"] = str(exc)
        raise
    finally:
        write_json_once(run_paths.payload_root/"audit-summary.json", summary)
        publish_split_manifests(run_paths, request_digest=digest, status=summary["status"],
            control_details={"payload_root": str(run_paths.payload_root), **summary}, payload_details=summary,
            control_artifacts=[run_paths.control_root/"request.json"],
            payload_artifacts=[p for p in run_paths.payload_root.rglob("*") if p.is_file()])
        pair = verify_split_manifests(run_paths.control_manifest, run_paths.payload_manifest)
        print(json.dumps({"run_id": run_paths.run_id, "integrity": pair["status"], **summary}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell", choices=("master", "aon"), required=True)
    parser.add_argument("--candidate-manifest", action="append", required=True)
    parser.add_argument("--timeout", type=float, default=240.)
    parser.add_argument("--recipe", help="explicit public-data recipe for the candidate experiment")
    options = parser.parse_args()
    if len(options.candidate_manifest) != 2 or not 0 < options.timeout <= 600:
        parser.error("supply exactly two candidates and a timeout in (0, 600]")
    run(options)
