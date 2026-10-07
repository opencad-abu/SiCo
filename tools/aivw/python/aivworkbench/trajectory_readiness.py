"""Describe missing system evidence for a sampled trajectory candidate.

This is a pre-integration assessment, not an AMS qualification executor.
Public block correlation cannot authorize replacing an electrical instance.
"""

import json
from pathlib import Path

from .manifest import resolve_indexed_artifact, verify_split_manifests
from .workspace import sha256_file, stable_digest


def assess_sampled_candidate(model, correlation, revision_report, *, adapter_manifest=None):
    if (model["model_class"] != "electrical.sampled_state" or
            model["source_generation"] != correlation["source_generation"] or
            model["experiment_digest"] != correlation["experiment_digest"] or
            revision_report["source_generation"] != model["source_generation"]):
        raise ValueError("system assessment inputs do not identify one source/experiment")
    revisions = revision_report["revisions"]
    if not revisions or revisions[-1]["gate_feedback"] is None:
        raise ValueError("system assessment requires evaluated revision evidence")
    final = revisions[-1]["gate_feedback"]["evidence"]
    if (final.get("candidate_source_sha256") != correlation["candidate_sha256"] or
            final.get("report_digest") != stable_digest(correlation)):
        raise ValueError("system assessment candidate and feedback differ")
    blockers = [
        {"code": "SCHEDULING_ADAPTER_REQUIRED", "detail":
         "Model state advances only when the controller testbench calls advance()."},
        {"code": "LOAD_NETWORK_ADAPTER_REQUIRED", "detail": model["load_boundary"]},
        {"code": "SUPPLY_CURRENT_NETWORK_UNSUPPORTED", "detail": model["current_boundary"]},
        {"code": "DYNAMIC_HOLDOUT_REPEAT_NOT_QUALIFIED", "detail":
         "Public calibration is not independent whole-case holdout plus two frozen repeats."},
        {"code": "SYSTEM_BINDING_AND_EXECUTION_MISSING", "detail":
         "No candidate-bound AMS config, connect-rule, system stimulus or system simulation evidence."},
        {"code": "LIVE_SOURCE_RECHECK_REQUIRED", "detail":
         "Historical source generation must be rechecked before live system substitution."},
    ]
    adapter = None
    if adapter_manifest is not None:
        adapter = read_adapter_certificate(adapter_manifest, correlation["candidate_sha256"], model["source_generation"])
        mechanical = {"SCHEDULING_ADAPTER_REQUIRED", "LOAD_NETWORK_ADAPTER_REQUIRED", "SUPPLY_CURRENT_NETWORK_UNSUPPORTED"}
        blockers = [b for b in blockers if b["code"] not in mechanical]
        blockers.append({"code": "NETWORK_SCOPE_LIMITED", "detail": adapter["scope"]})
    if correlation["status"] != "PASS":
        blockers.append({"code": "DYNAMIC_CORRELATION_NOT_ACCEPTED", "detail": correlation["status"]})
    if correlation["diagnostic_status"] != "PASS":
        blockers.append({"code": "PUBLIC_DYNAMIC_MODEL_ERRORS", "detail": correlation["diagnostic_status"]})
    return {"schema_version": 1, "kind": "sampled-candidate-system-assessment",
            "status": "BLOCKED_SYSTEM_INTEGRATION", "system_simulation": "NOT_RUN",
            "source_generation": model["source_generation"],
            "candidate_sha256": correlation["candidate_sha256"],
            "correlation_digest": stable_digest(correlation),
            "revision_report_digest": stable_digest(revision_report),
            "adapter_certificate": adapter,
            "unsupported": model["unsupported"], "blockers": blockers,
            "recommended_binding": "retain_original_analog_instance",
            "qualification": "NOT_ESTABLISHED", "publication": "NOT_REQUESTED"}


def read_adapter_certificate(control_path, candidate_digest, source_generation):
    control_path = Path(control_path)
    control = json.loads(control_path.read_text())
    root = Path(control["details"]["payload_root"])
    pair = verify_split_manifests(control_path, root/"payload-manifest.json")
    summary_path = resolve_indexed_artifact(control_path, "summary.json").path
    summary = json.loads(summary_path.read_text())
    candidate = resolve_indexed_artifact(control_path, "candidate.sv").path
    adapter = resolve_indexed_artifact(control_path, "adapter.sv").path
    package = resolve_indexed_artifact(control_path, "EE_pkg.sv").path
    if (pair["run_status"] != "PASS_ADAPTER_MECHANICS" or summary["status"] != pair["run_status"] or
            summary["candidate_sha256"] != candidate_digest or sha256_file(candidate) != candidate_digest or
            summary["adapter_sha256"] != sha256_file(adapter) or summary["package_sha256"] != sha256_file(package) or
            summary["source_generation"] != source_generation or
            {c["case"] for c in summary["checks"]} != {"passive_network", "outside_load", "active_load", "ideal_driver"} or
            any(c["status"] != "PASS" for c in summary["checks"])):
        raise ValueError("adapter certificate is incomplete or belongs to another candidate")
    return {"run_id": pair["run_id"], "control_manifest_sha256": pair["control_manifest_sha256"],
            "candidate_sha256": candidate_digest, "adapter_sha256": summary["adapter_sha256"],
            "scope": summary["scope"], "status": "PASS_ADAPTER_MECHANICS"}
