"""Public-only prerequisites for a future frozen dynamic evaluation.

This audit neither opens private reservations nor freezes/executes a candidate.
Even PUBLIC_PREREQUISITES_MET is not an evaluator capability or qualification.
Run chronology is local recorded provenance, not a trusted timestamp service.
"""

from datetime import datetime
import json
from pathlib import Path

from ..electrical_experiment import validate_experiment
from ..executors.trajectory_correlation import correlation_report
from ..manifest import resolve_indexed_artifact, verify_split_manifests
from ..plugins.sampled_state_sv import render_model
from ..waveform_metrics import read_waveform, validate_waveform_policy
from ..workspace import sha256_file, stable_digest


_COMMITMENT_FIELDS = {"schema_version", "locked", "public_values_included", "case_count",
                      "commitment_sha256", "domain_digest", "policy_digest", "state"}
_SCOPES = {"single_event": "single_event_multirate_slice_only",
           "sequence": "sequential_multirate_slice_only"}


def _time(run_id):
    return datetime.strptime(run_id.split("-", 1)[0], "%Y%m%dT%H%M%S.%fZ")


def _run(control_path):
    control_path = Path(control_path)
    value = json.loads(control_path.read_text())
    root = Path(value["details"]["payload_root"])
    pair = verify_split_manifests(control_path, root/"payload-manifest.json")
    ref = {"run_id": pair["run_id"], "control_manifest": str(control_path.resolve()),
           "sha256": pair["control_manifest_sha256"], "run_status": pair["run_status"]}
    return ref


def _artifact(control, relative):
    return resolve_indexed_artifact(Path(control), relative).path


def _json(control, relative):
    return json.loads(_artifact(control, relative).read_text())


def inspect_public_candidate(control, acceptance_path, experiment_path):
    """Recompute the physical correlation from authenticated public waveforms."""
    reference = _run(control)
    plan_path = _artifact(control, "checks/gates/public_data/experiment.json")
    plan = validate_experiment(json.loads(plan_path.read_text()))
    experiment_path = Path(experiment_path)
    experiment_bytes = experiment_path.read_bytes()
    if stable_digest(validate_experiment(json.loads(experiment_bytes))) != stable_digest(plan):
        raise ValueError("selected experiment differs from candidate experiment")
    evidence = _json(control, "checks/gates/public_data/characterization-evidence.json")
    model = _json(control, "checks/gates/model/model.json")
    candidate = _artifact(control, "checks/gates/model/candidate.sv")
    rnm = _json(control, "checks/gates/rnm/trajectory-evidence.json")
    policy_path = _artifact(control, "checks/gates/correlation/acceptance-policy.json")
    policy = validate_waveform_policy(json.loads(policy_path.read_text()))
    # Reservation commitments hash the original policy bytes. The gate writes
    # a normalized JSON copy, so compare semantics before binding those bytes.
    acceptance_path = Path(acceptance_path)
    acceptance_bytes = acceptance_path.read_bytes()
    accepted_policy = validate_waveform_policy(json.loads(acceptance_bytes))
    if stable_digest(accepted_policy) != stable_digest(policy):
        raise ValueError("selected policy differs from candidate correlation policy")
    old = _json(control, "checks/gates/correlation/correlation.json")
    identity = dict(model)
    digest = identity.pop("model_digest")
    expected_cases = [c["id"] for c in plan["cases"]]
    if (evidence["dataset"] != "public_calibration" or model["dataset"] != "public_calibration" or
            evidence["source_recheck"] != "PASS" or evidence["target"] != plan["target"] or
            evidence["experiment_sha256"] != sha256_file(plan_path) or
            stable_digest(identity) != digest or render_model(model) != candidate.read_text() or
            any(x["experiment_digest"] != stable_digest(plan) for x in (model, evidence, rnm)) or
            any(x["source_generation"] != evidence["source_generation"] for x in (model, rnm)) or
            rnm["candidate_sha256"] != sha256_file(candidate) or rnm["status"] != "EXECUTED" or
            [c["id"] for c in evidence["cases"]] != expected_cases or
            [c["id"] for c in rnm["cases"]] != expected_cases):
        raise ValueError("public candidate/source/experiment identity mismatch")
    golden, actual = {}, {}
    for case, source, simulated in zip(plan["cases"], evidence["cases"], rnm["cases"]):
        if any(record["case_digest"] != stable_digest(case) for record in (source, simulated)):
            raise ValueError("public case binding differs")
        csvs = [a for a in source["artifacts"] if a["path"].endswith("/waveforms.csv")]
        if len(csvs) != 1:
            raise ValueError("one public source waveform required")
        src = _artifact(control, csvs[0]["path"])
        sim = _artifact(control, simulated["csv"])
        if sha256_file(src) != csvs[0]["sha256"] or sha256_file(sim) != simulated["sha256"]:
            raise ValueError("public waveform hash mismatch")
        signals = {p: "V" for p in plan["target"]["term_order"]}
        signals["VPROBE:p"] = "A"
        golden[case["id"]] = read_waveform(src, signals, plan["analysis"]["stop_s"])
        actual[case["id"]] = read_waveform(sim, {plan["roles"]["output"]: "V", "VPROBE:p": "A"}, plan["analysis"]["stop_s"])
    computed = correlation_report(plan, evidence, golden, actual, policy, sha256_file(candidate))
    if stable_digest(computed) != stable_digest(old):
        raise ValueError("stored correlation differs from independently recomputed public evidence")
    if _run(control) != reference:
        raise ValueError("candidate manifest changed during audit")
    if acceptance_path.read_bytes() != acceptance_bytes:
        raise ValueError("acceptance policy changed during audit")
    if experiment_path.read_bytes() != experiment_bytes:
        raise ValueError("experiment changed during audit")
    return {"reference": reference, "target": plan["target"], "source_generation": evidence["source_generation"],
            "experiment_digest": stable_digest(plan), "plan_file_sha256": sha256_file(experiment_path),
            "plan_artifact_sha256": sha256_file(plan_path),
            "candidate_sha256": sha256_file(candidate), "policy_sha256": sha256_file(acceptance_path),
            "policy_artifact_sha256": sha256_file(policy_path),
            "policy_status": policy["status"], "correlation_status": computed["status"],
            "correlation_digest": stable_digest(computed), "case_count": len(computed["cases"]),
            "failed_public_cases": [c["id"] for c in computed["cases"] if c["diagnostic_status"] != "PASS"]}


def inspect_reservation(control):
    """Read only the indexed public commitment, never private paths or vectors."""
    reference = _run(control)
    summary = _json(control, "public-commitment.json")
    commitment = summary["commitment"]
    if (reference["run_status"] != "RESERVED_NOT_EVALUATED" or
            summary["status"] != "RESERVED_NOT_EVALUATED" or
            summary["kind"] != "dynamic-holdout-reservation" or
            set(commitment) != _COMMITMENT_FIELDS or commitment["schema_version"] != 1 or
            commitment["locked"] is not True or commitment["public_values_included"] is not False or
            type(commitment["case_count"]) is not int or not 1 <= commitment["case_count"] <= 256 or
            commitment["state"] != "reserved_before_fit" or
            commitment["domain_digest"] != stable_digest(summary["domain"]) or
            commitment["policy_digest"] != summary["policy_sha256"]):
        raise ValueError("invalid public reservation commitment")
    for key in ("commitment_sha256", "domain_digest", "policy_digest"):
        digest = commitment[key]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid public reservation digest")
    if _run(control) != reference:
        raise ValueError("reservation manifest changed during audit")
    return {"reference": reference, "commitment": commitment, "domain": summary["domain"]}


def assess_public_prerequisites(candidate, reservations):
    """Assess authenticated records; report every applicable public blocker."""
    blockers = []
    def block(code, detail):
        blockers.append({"code": code, "detail": detail})
    if candidate["policy_status"] != "approved":
        block("ACCEPTANCE_POLICY_NOT_APPROVED", "Absolute voltage/current/time floors remain a proposal.")
    if candidate["failed_public_cases"]:
        block("PUBLIC_CORRELATION_FAILED", list(candidate["failed_public_cases"]))
    if candidate["correlation_status"] != "PASS":
        block("PUBLIC_CORRELATION_NOT_ACCEPTED", candidate["correlation_status"])
    if set(reservations) != set(_SCOPES):
        raise ValueError("single-event and sequence reservations are both required")
    for kind, reserved in reservations.items():
        if reserved["domain"]["scope"] != _SCOPES[kind]:
            block("RESERVATION_SCOPE_MISMATCH", kind)
        if reserved["domain"]["experiment_sha256"] != candidate["plan_file_sha256"]:
            block("RESERVATION_EXPERIMENT_MISMATCH", kind)
        if reserved["commitment"]["policy_digest"] != candidate["policy_sha256"]:
            block("RESERVATION_POLICY_MISMATCH", kind)
        if _time(reserved["reference"]["run_id"]) >= _time(candidate["reference"]["run_id"]):
            block("RESERVATION_NOT_BEFORE_CANDIDATE_RUN", kind)
    return {"schema_version": 1, "kind": "dynamic-holdout-public-prerequisites",
            "status": "BLOCKED_HOLDOUT_ADMISSION" if blockers else "PUBLIC_PREREQUISITES_MET",
            "candidate": candidate, "reservations": reservations, "blockers": blockers,
            "recommended_binding": "retain_original_analog_instance",
            "disposition": "ANALOG_ISLAND" if candidate["failed_public_cases"] else "UNQUALIFIED_CANDIDATE",
            "qualification": "NOT_ESTABLISHED", "private_access_authorized": False,
            "candidate_freeze": "NOT_PERFORMED", "holdout_evaluated": False,
            "chronology_scope": "local_recorded_run_order_not_independent_prefit_attestation",
            "remaining_evaluator_requirements": ["qualified_private_execution_boundary",
                "one_immutable_candidate_freeze", "current_source_recheck", "two_frozen_source_candidate_repeats"],
            "system_substitution": "NOT_PERFORMED", "publication": "NOT_PERFORMED"}


def audit_admission(candidate_control, single_control, sequence_control, acceptance_path, experiment_path):
    candidate = inspect_public_candidate(candidate_control, acceptance_path, experiment_path)
    reservations = {"single_event": inspect_reservation(single_control),
                    "sequence": inspect_reservation(sequence_control)}
    return assess_public_prerequisites(candidate, reservations)
