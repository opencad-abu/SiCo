"""Validate one topology complete G0-G7 evidence record."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .ldo_qualification_artifacts import (
    qualification_validate_artifacts,
    qualification_validate_manifest_pair,
)
from .ldo_qualification_secrets import scan_secret_leaks
from .ldo_qualification_status import (
    LDO_GATES,
    LDO_TOPOLOGIES,
    M3_ANALOG_ISLAND,
    M3_BLOCKED_ENVIRONMENT,
    M3_BLOCKED_INPUT,
    M3_FAIL,
    M3_NEEDS_MORE_EVIDENCE,
    M3_STALE_SOURCE,
    qualification_GATE_STATUS,
)
from .ldo_qualification_values import (
    qualification_copy_json,
    qualification_digest,
    qualification_reject_verdict_fields,
    qualification_valid_digest,
)


def _gate_result(raw: object, gate_id: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("%s gate result must be an object" % gate_id)
    value = qualification_copy_json(raw)
    qualification_reject_verdict_fields(value, gate_id)
    status = value.get("status")
    if not isinstance(status, str) or status not in qualification_GATE_STATUS:
        raise ValueError("%s gate status is invalid" % gate_id)
    value["status"] = status
    value.setdefault("gate_id", gate_id)
    value["result_sha256"] = qualification_digest({key: item for key, item in value.items() if key != "result_sha256"})
    return value


def validate_topology_evidence(
    evidence: Mapping[str, Any],
    *,
    topology: str,
    source_snapshot: Mapping[str, Any],
    artifact_root: str | Path | None = None,
    expected_template_lock: str | None = None,
) -> dict[str, Any]:
    """Validate one topology's complete G0-G7 evidence record."""

    findings: list[str] = []
    if topology not in LDO_TOPOLOGIES:
        return {"status": M3_BLOCKED_INPUT, "topology": topology, "findings": ["unsupported topology"]}
    try:
        value = qualification_copy_json(evidence)
        qualification_reject_verdict_fields(value)
    except ValueError as exc:
        return {"status": M3_BLOCKED_INPUT, "topology": topology, "findings": [str(exc)]}
    if not isinstance(value, Mapping):
        return {"status": M3_BLOCKED_INPUT, "topology": topology, "findings": ["topology evidence must be an object"]}
    if value.get("schema_version") != 1:
        findings.append("topology evidence schema_version must be 1")
    if value.get("topology") != topology:
        findings.append("topology identity does not match evidence")
    source_generation = source_snapshot.get("source_generation")
    if not qualification_valid_digest(source_generation):
        findings.append("source snapshot has no valid source_generation")
    elif value.get("source_generation") != source_generation:
        findings.append("topology evidence source_generation does not match source snapshot")
    if source_snapshot.get("authenticated") is not True or source_snapshot.get("status") != "PASS":
        findings.append("authenticated source snapshot is not PASS")
    if source_snapshot.get("scope") == "recipe_target":
        findings.append("one recipe target snapshot cannot establish M3 family qualification")
    gates_raw = value.get("gates")
    gates: dict[str, dict[str, Any]] = {}
    if not isinstance(gates_raw, Mapping) or set(str(key) for key in gates_raw) != set(LDO_GATES):
        findings.append("topology evidence must contain exactly G0-G7")
    else:
        for gate_id in LDO_GATES:
            try:
                gates[gate_id] = _gate_result(gates_raw[gate_id], gate_id)
            except ValueError as exc:
                findings.append(str(exc))
    gate_statuses = {gate_id: item.get("status") for gate_id, item in gates.items()}
    failed_gate = next((gate_id for gate_id, status in gate_statuses.items() if isinstance(status, str) and status.startswith("FAIL")), None)
    blocked_gate = next((gate_id for gate_id, status in gate_statuses.items() if status in {M3_BLOCKED_INPUT, M3_BLOCKED_ENVIRONMENT, M3_STALE_SOURCE}), None)
    analog_gate = next((gate_id for gate_id, status in gate_statuses.items() if status == M3_ANALOG_ISLAND), None)
    template_lock = value.get("template_lock")
    if template_lock is not None and not qualification_valid_digest(template_lock):
        findings.append("template_lock is malformed")
    if expected_template_lock is not None and template_lock != expected_template_lock:
        findings.append("template_lock does not match expected lock")
    for name in ("candidate_sha256", "interface_digest", "gate_result_sha256"):
        if name in value and value[name] is not None and not qualification_valid_digest(value[name]):
            findings.append("%s is malformed" % name)
    _, artifact_findings = qualification_validate_artifacts(value.get("artifacts"), artifact_root=artifact_root)
    findings.extend(artifact_findings)
    _, manifest_findings = qualification_validate_manifest_pair(value.get("manifest_pair"))
    findings.extend(manifest_findings)
    holdout = value.get("holdout")
    if not isinstance(holdout, Mapping) or holdout.get("locked") is not True or holdout.get("public_values_included") is not False:
        findings.append("topology evidence holdout is not locked")
    secret_findings = scan_secret_leaks(value)
    findings.extend("secret material at %s" % item for item in secret_findings)
    normalized = {
        "schema_version": 1,
        "topology": topology,
        "source_generation": value.get("source_generation"),
        "template_lock": template_lock,
        "candidate_sha256": value.get("candidate_sha256"),
        "interface_digest": value.get("interface_digest"),
        "gates": gates,
        "artifacts": value.get("artifacts"),
        "manifest_pair": value.get("manifest_pair"),
        "holdout": holdout,
        "run_id": value.get("run_id"),
    }
    gate_result_sha256 = qualification_digest({key: item for key, item in normalized.items() if key != "gate_result_sha256"})
    normalized["gate_result_sha256"] = gate_result_sha256
    if value.get("gate_result_sha256") is not None and value.get("gate_result_sha256") != gate_result_sha256:
        findings.append("gate_result_sha256 does not match normalized gates")
    if failed_gate is not None:
        status = M3_FAIL
    elif blocked_gate is not None or findings:
        status = M3_BLOCKED_ENVIRONMENT if any("authenticated source" in item for item in findings) and not artifact_findings else M3_BLOCKED_INPUT
    elif analog_gate is not None:
        status = M3_ANALOG_ISLAND
    elif any(status != "PASS" for status in gate_statuses.values()):
        status = M3_NEEDS_MORE_EVIDENCE
    else:
        status = "PASS"
    return {
        "status": status,
        "topology": topology,
        "run_id": value.get("run_id"),
        "source_generation": value.get("source_generation"),
        "template_lock": template_lock,
        "candidate_sha256": value.get("candidate_sha256"),
        "interface_digest": value.get("interface_digest"),
        "gate_result_sha256": gate_result_sha256,
        "gate_statuses": gate_statuses,
        "findings": sorted(set(findings)),
        "artifact_count": len(value.get("artifacts", [])) if isinstance(value.get("artifacts"), list) else 0,
        "manifest_pair_status": value.get("manifest_pair", {}).get("status") if isinstance(value.get("manifest_pair"), Mapping) else None,
        "normalized": normalized,
    }
