"""Producer-bound physical-time correlation and deterministic Agent feedback."""

import json

from ..executor import ExecutorResult
from ..trajectory_evidence import indexed_file, public_trajectories
from ..waveform_metrics import compare_trajectories, read_waveform, validate_waveform_policy
from ..workspace import sha256_file, stable_digest, write_json_once
from .trajectory_xcelium import model_inputs


def correlation_report(plan, evidence, reference, candidate, policy, candidate_sha256):
    validate_waveform_policy(policy)
    if set(candidate) != set(reference) or set(reference) != {c["id"] for c in plan["cases"]}:
        raise ValueError("trajectory correlation case coverage differs")
    cases = [{"id": c["id"], "case_digest": stable_digest(c), **compare_trajectories(
        candidate[c["id"]], reference[c["id"]], output=plan["roles"]["output"], current="VPROBE:p",
        events=c["events"], definition=plan["measurement"], policy=policy)} for c in plan["cases"]]
    passed = all(c["diagnostic_status"] == "PASS" for c in cases)
    return {"schema_version": 1, "kind": "public-trajectory-correlation",
            "status": ("PASS" if passed else "FAIL_CORRELATION") if policy["status"] == "approved" else "BLOCKED_CONTRACT",
            "diagnostic_status": "PASS" if passed else "FAIL_CORRELATION", "cases": cases,
            "source_generation": evidence["source_generation"], "experiment_digest": stable_digest(plan),
            "candidate_sha256": candidate_sha256, "policy_digest": stable_digest(policy),
            "dataset": "public_calibration", "holdout": "NOT_EVALUATED", "repeat": "NOT_EVALUATED",
            "qualification": "NOT_ESTABLISHED", "product_current_timing_status": "BLOCKED_CONTRACT",
            "system_readiness": "BLOCKED_DYNAMIC_QUALIFICATION"}


def feedback_from_report(report):
    from ..agent.qualification import GateFeedback
    failed = [c["id"] for c in report["cases"] if c["diagnostic_status"] != "PASS"]
    # A proposal cannot yield PASS, even for a structurally valid candidate.
    status = "BLOCKED" if report["status"].startswith("BLOCKED_") else "FAIL" if failed else "PASS"
    return GateFeedback(status, "physical_trajectory_correlation", {
        "correlation_status": report["status"], "diagnostic_status": report["diagnostic_status"],
        "failed_cases": failed, "qualification": "NOT_ESTABLISHED", "dataset": report["dataset"]}, {
        "candidate_source_sha256": report["candidate_sha256"], "source_generation": report["source_generation"],
        "experiment_digest": report["experiment_digest"], "policy_digest": report["policy_digest"],
        "report_digest": stable_digest(report)})


def run_trajectory_correlation(context):
    try:
        plan, evidence, reference = public_trajectories(context)
        model, source = model_inputs(context)
        producers = [d for d in context.dependencies.values() if "rnm_trajectories" in d.outputs]
        if len(producers) != 1 or producers[0].status != "PASS":
            raise ValueError("one successful RNM trajectory producer required")
        d = producers[0]
        path = indexed_file(context, d, d.outputs["rnm_trajectories"], d.outputs["rnm_trajectories_sha256"])
        rnm = json.loads(path.read_text())
        if (rnm["kind"] != "xcelium-trajectories" or rnm["status"] != "EXECUTED" or
                rnm["experiment_digest"] != stable_digest(plan) or rnm["source_generation"] != evidence["source_generation"] or
                model["source_generation"] != evidence["source_generation"] or
                rnm["candidate_sha256"] != sha256_file(source)):
            raise ValueError("RNM/golden/candidate identities differ")
        if [r["id"] for r in rnm["cases"]] != [c["id"] for c in plan["cases"]]:
            raise ValueError("RNM case identities differ")
        waves = {}
        for c, r in zip(plan["cases"], rnm["cases"]):
            if r["case_digest"] != stable_digest(c):
                raise ValueError("RNM stimulus differs")
            csv = indexed_file(context, d, r["csv"], r["sha256"])
            waves[c["id"]] = read_waveform(csv, {plan["roles"]["output"]: "V", "VPROBE:p": "A"}, plan["analysis"]["stop_s"])
        policy = json.loads(context.recipe.input_path("dynamic_acceptance").read_text())
        policy_path = context.gate_root/"acceptance-policy.json"
        write_json_once(policy_path, policy)
        report = correlation_report(plan, evidence, reference, waves, policy, sha256_file(source))
        output = context.gate_root/"correlation.json"
        write_json_once(output, report)
        feedback = context.gate_root/"gate-feedback.json"
        write_json_once(feedback, feedback_from_report(report).to_dict())
        return ExecutorResult(report["status"], {"diagnostic_status": report["diagnostic_status"],
            "case_count": len(report["cases"]), "qualification": "NOT_ESTABLISHED"},
            {"dynamic_correlation": str(output.relative_to(context.run.payload_root)), "dynamic_correlation_sha256": sha256_file(output),
             "gate_feedback": str(feedback.relative_to(context.run.payload_root)), "gate_feedback_sha256": sha256_file(feedback),
             "source_generation": evidence["source_generation"]}, (policy_path, output, feedback))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return ExecutorResult("BLOCKED_EVIDENCE", {"code": "trajectory_correlation_invalid", "reason": str(exc)})
