"""Aggregate both topologies into one qualification result."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .agent.context import redact_secrets
from .ldo_qualification_evidence import validate_topology_evidence
from .ldo_qualification_holdout import validate_holdout_blindness
from .ldo_qualification_repeat import compare_repeat_runs
from .ldo_qualification_status import (
    LDO_TOPOLOGIES,
    M3_ANALOG_ISLAND,
    M3_BLOCKED_ENVIRONMENT,
    M3_BLOCKED_INPUT,
    M3_FAIL,
    M3_NEEDS_MORE_EVIDENCE,
    M3_QUALIFIED,
    M3_SCHEMA_VERSION,
)
from .ldo_qualification_values import (
    qualification_copy_json,
    qualification_digest,
    qualification_now,
    qualification_reject_verdict_fields,
)


def aggregate_ldo_qualification(
    topology_evidence: Mapping[str, Sequence[Mapping[str, Any]] | Mapping[str, Any]],
    *,
    source_snapshot: Mapping[str, Any],
    repeat_runs: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    public_plans: Mapping[str, Mapping[str, Any]] | None = None,
    hidden_holdout: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate both topology reports into one fail-closed M3 result."""

    findings: list[str] = []
    try:
        snapshot = qualification_copy_json(source_snapshot)
        qualification_reject_verdict_fields(snapshot)
    except ValueError as exc:
        return {"schema_version": M3_SCHEMA_VERSION, "status": M3_BLOCKED_INPUT, "findings": [str(exc)]}
    topology_reports: dict[str, Any] = {}
    repeats: dict[str, Any] = {}
    plans: dict[str, Any] = {}
    for topology in LDO_TOPOLOGIES:
        raw = topology_evidence.get(topology) if isinstance(topology_evidence, Mapping) else None
        if isinstance(raw, Mapping):
            entries: Sequence[Mapping[str, Any]] = (raw,)
        elif isinstance(raw, (list, tuple)):
            entries = tuple(item for item in raw if isinstance(item, Mapping))
        else:
            entries = ()
        if not entries:
            topology_reports[topology] = {"status": M3_NEEDS_MORE_EVIDENCE, "topology": topology, "findings": ["topology evidence is missing"]}
            continue
        validated = [validate_topology_evidence(item, topology=topology, source_snapshot=snapshot) for item in entries]
        # A topology is independently qualified only when every supplied run
        # is valid.  The first report is the canonical evidence summary.
        primary = dict(validated[0])
        if any(item.get("status") != "PASS" for item in validated):
            statuses = [str(item.get("status")) for item in validated]
            if any(item == M3_ANALOG_ISLAND for item in statuses):
                primary["status"] = M3_ANALOG_ISLAND
            elif any(item.startswith("FAIL") for item in statuses):
                primary["status"] = M3_FAIL
            elif any(item == M3_BLOCKED_ENVIRONMENT for item in statuses):
                primary["status"] = M3_BLOCKED_ENVIRONMENT
            else:
                primary["status"] = M3_BLOCKED_INPUT
            primary["findings"] = sorted(set(item for report in validated for item in report.get("findings", ())))
        primary["run_reports"] = validated
        topology_reports[topology] = primary
        selected_repeat = repeat_runs.get(topology) if isinstance(repeat_runs, Mapping) else None
        if selected_repeat is None and len(entries) >= 2:
            selected_repeat = tuple(
                {
                    "status": report.get("status"),
                    "run_id": report.get("run_id"),
                    "source_generation": report.get("source_generation"),
                    "template_lock": report.get("template_lock"),
                    "candidate_sha256": report.get("candidate_sha256"),
                    "gate_result_sha256": report.get("gate_result_sha256"),
                }
                for report in validated
            )
        repeats[topology] = compare_repeat_runs(selected_repeat or ())
        if isinstance(public_plans, Mapping) and isinstance(public_plans.get(topology), Mapping):
            plans[topology] = validate_holdout_blindness(public_plans[topology], hidden_holdout=hidden_holdout)
        else:
            plans[topology] = {"status": M3_NEEDS_MORE_EVIDENCE, "findings": ["public calibration plan is missing"]}
    all_plan_findings = [item for report in plans.values() for item in report.get("findings", ())]
    findings.extend(all_plan_findings)
    repeat_failures = [item for item in repeats.values() if item.get("status") != "PASS"]
    topology_statuses = {name: report.get("status") for name, report in topology_reports.items()}
    if snapshot.get("status") == M3_BLOCKED_ENVIRONMENT or snapshot.get("authenticated") is not True:
        final_status = M3_BLOCKED_ENVIRONMENT
        findings.append("authenticated Virtuoso snapshot is required before analog qualification")
    elif any(status == M3_FAIL for status in topology_statuses.values()) or any(str(status).startswith("FAIL") for status in topology_statuses.values()):
        final_status = M3_FAIL
    elif any(status == M3_ANALOG_ISLAND for status in topology_statuses.values()):
        final_status = M3_ANALOG_ISLAND
    elif any(status == M3_BLOCKED_ENVIRONMENT for status in topology_statuses.values()):
        final_status = M3_BLOCKED_ENVIRONMENT
    elif any(status == M3_BLOCKED_INPUT for status in topology_statuses.values()) or repeat_failures or any(report.get("status") != "PASS" for report in plans.values()):
        final_status = M3_NEEDS_MORE_EVIDENCE if not any(status == M3_BLOCKED_INPUT for status in topology_statuses.values()) else M3_BLOCKED_INPUT
    elif all(status == "PASS" for status in topology_statuses.values()) and not repeat_failures and all(report.get("status") == "PASS" for report in plans.values()):
        final_status = M3_QUALIFIED
    else:
        final_status = M3_NEEDS_MORE_EVIDENCE
    report = {
        "schema_version": M3_SCHEMA_VERSION,
        "kind": "ldo-m3-qualification",
        "status": final_status,
        "started_at": qualification_now(),
        "finished_at": qualification_now(),
        "source_snapshot": snapshot,
        "topologies": topology_reports,
        "repeat": repeats,
        "blindness": plans,
        "metadata": {} if metadata is None else redact_secrets(qualification_copy_json(metadata)),
        "findings": sorted(set(findings)),
        "promotion": "NOT_AUTHORIZED",
        "analog_verdict_authority": "Spectre/Xcelium/deterministic-gates-and-approved-evidence",
    }
    report["report_sha256"] = qualification_digest({key: value for key, value in report.items() if key != "report_sha256"})
    return report
