"""Freeze one admitted candidate and verify the immutable freeze record.

A freeze binds the exact candidate bytes, source generation, approved policy,
experiment and the authenticated admission evidence.  It is public record
only: no private reservation payload is opened and no EDA is executed.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..workspace import allocate_split_run, resolve_launch_paths, sha256_file, stable_digest, write_json_once
from ..manifest import publish_split_manifests, verify_split_manifests
from .dynamic_admission import _artifact, _json, _run


_FREEZE_FIELDS = {"schema_version", "kind", "status", "target", "candidate_sha256",
                  "source_generation", "policy_sha256", "experiment_sha256", "admission",
                  "sources", "reservations", "qualification", "holdout_evaluated",
                  "private_access_authorized"}
_PAIR_FIELDS = {"run_id", "control_manifest", "control_manifest_sha256", "run_status"}


def _pair(control):
    reference = _run(control)
    return {"run_id": reference["run_id"], "control_manifest": reference["control_manifest"],
            "control_manifest_sha256": reference["sha256"], "run_status": reference["run_status"]}


def freeze_candidate(matrix_control, reference_control, crosscheck_control, source_control,
                     single_control, sequence_control, admission_control, policy_path, experiment_path):
    """Freeze the admitted candidate with all accepted evidence bindings."""

    admission_summary = _json(admission_control, "summary.json")
    if admission_summary.get("status") != "PUBLIC_PREREQUISITES_MET" or admission_summary.get("blockers") != []:
        raise ValueError("admission did not reach PUBLIC_PREREQUISITES_MET")
    candidate = admission_summary["candidate"]
    matrix_pair = _pair(matrix_control)
    if candidate["reference"]["run_id"] != matrix_pair["run_id"] or candidate["reference"]["sha256"] != matrix_pair["control_manifest_sha256"]:
        raise ValueError("admission candidate run differs from the freeze candidate run")
    candidate_sha = sha256_file(_artifact(matrix_control, "xcelium/candidate.sv"))
    if candidate_sha != candidate["candidate_sha256"]:
        raise ValueError("frozen candidate bytes differ from the admitted candidate")
    if sha256_file(Path(policy_path)) != candidate["policy_sha256"] or sha256_file(Path(experiment_path)) != candidate["plan_file_sha256"]:
        raise ValueError("freeze policy or experiment differs from the admitted decision")
    admission_pair = _pair(admission_control)
    record = {"schema_version": 1, "kind": "candidate-freeze", "status": "FROZEN_NOT_EVALUATED",
              "target": candidate["target"], "candidate_sha256": candidate_sha,
              "source_generation": candidate["source_generation"],
              "policy_sha256": candidate["policy_sha256"],
              "experiment_sha256": candidate["plan_file_sha256"],
              "admission": {"run_id": admission_pair["run_id"],
                            "control_manifest_sha256": admission_pair["control_manifest_sha256"],
                            "summary_sha256": sha256_file(_artifact(admission_control, "summary.json")),
                            "status": admission_summary["status"]},
              "sources": {"matrix": matrix_pair, "source": _pair(source_control),
                          "reference": _pair(reference_control), "crosscheck": _pair(crosscheck_control)},
              "reservations": {"single_event": _pair(single_control), "sequence": _pair(sequence_control)},
              "qualification": "NOT_ESTABLISHED", "holdout_evaluated": False,
              "private_access_authorized": False}
    request = {"kind": "candidate-freeze", "candidate_sha256": candidate_sha,
               "policy_sha256": candidate["policy_sha256"], "admission_digest": stable_digest(record["admission"]),
               "sources": record["sources"], "reservations": record["reservations"]}
    digest = stable_digest(request)
    run = allocate_split_run(resolve_launch_paths(), Path("/path/to/xh/amsVerify/data/ams").resolve(),
        library=candidate["target"]["library"], cell=candidate["target"]["cell"], view="candidate_freeze",
        kind="candidate-freeze", request_digest=digest)
    write_json_once(run.control_root/"request.json", request)
    write_json_once(run.payload_root/"candidate-freeze.json", record)
    publish_split_manifests(run, request_digest=digest, status=record["status"],
        control_details={"payload_root": str(run.payload_root), "candidate_sha256": candidate_sha},
        payload_details={"scope": "immutable_candidate_freeze_public_evidence_only"},
        control_artifacts=[run.control_root/"request.json"],
        payload_artifacts=[run.payload_root/"candidate-freeze.json"])
    pair = verify_split_manifests(run.control_manifest, run.payload_manifest)
    return {"run_id": run.run_id, "control_manifest": str(run.control_manifest),
            "integrity": pair["status"], "record": record}


def inspect_freeze(control, candidate):
    """Verify the freeze record against one admitted candidate."""

    reference = _run(control)
    record = _json(control, "candidate-freeze.json")
    if set(record) != _FREEZE_FIELDS or record["schema_version"] != 1:
        raise ValueError("freeze record fields differ")
    if record["kind"] != "candidate-freeze" or reference["run_status"] != "FROZEN_NOT_EVALUATED":
        raise ValueError("freeze record status is invalid")
    if record["status"] != "FROZEN_NOT_EVALUATED" or record["qualification"] != "NOT_ESTABLISHED":
        raise ValueError("freeze record scope is invalid")
    if record["holdout_evaluated"] is not False or record["private_access_authorized"] is not False:
        raise ValueError("freeze record overstates its scope")
    if (record["candidate_sha256"] != candidate["candidate_sha256"] or
            record["policy_sha256"] != candidate["policy_sha256"] or
            record["source_generation"] != candidate["source_generation"] or
            record["target"] != candidate["target"]):
        raise ValueError("freeze record belongs to another candidate decision")
    if record["sources"]["matrix"]["run_id"] != candidate["reference"]["run_id"] or \
            record["sources"]["matrix"]["control_manifest_sha256"] != candidate["reference"]["sha256"]:
        raise ValueError("freeze matrix source differs from the admitted candidate run")
    for label, pair in record["sources"].items():
        if set(pair) != _PAIR_FIELDS:
            raise ValueError(f"freeze {label} source fields differ")
    for label, pair in record["reservations"].items():
        if set(pair) != _PAIR_FIELDS:
            raise ValueError(f"freeze {label} reservation fields differ")
    if _run(control) != reference:
        raise ValueError("freeze manifest changed during audit")
    return record
