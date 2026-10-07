"""Compare independently identified qualification runs."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .ldo_qualification_status import (
    M3_BLOCKED_INPUT,
    M3_FAIL_REPEAT,
    M3_NEEDS_MORE_EVIDENCE,
)
from .ldo_qualification_values import qualification_digest, qualification_valid_digest


def compare_repeat_runs(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Require two independent runs with byte-identical qualification keys."""

    findings: list[str] = []
    if not isinstance(runs, (list, tuple)) or len(runs) < 2:
        return {"status": M3_NEEDS_MORE_EVIDENCE, "repeat_consistent": False, "run_count": 0 if not isinstance(runs, (list, tuple)) else len(runs), "findings": ["at least two independent runs are required"]}
    keys: list[dict[str, Any]] = []
    run_ids: set[str] = set()
    for index, run in enumerate(runs):
        if not isinstance(run, Mapping):
            findings.append("repeat run %d is not an object" % index)
            continue
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id or run_id in run_ids:
            findings.append("repeat run %d has a duplicate or invalid run_id" % index)
        else:
            run_ids.add(run_id)
        if run.get("status") not in {"PASS", "QUALIFIED"}:
            findings.append("repeat run %d did not PASS" % index)
        source_generation = run.get("source_generation")
        template_lock = run.get("template_lock")
        candidate = run.get("candidate_sha256")
        gate_hash = run.get("gate_result_sha256")
        for label, item in (("source_generation", source_generation), ("candidate_sha256", candidate), ("gate_result_sha256", gate_hash)):
            if not qualification_valid_digest(item):
                findings.append("repeat run %d %s is missing or malformed" % (index, label))
        if template_lock is not None and not qualification_valid_digest(template_lock):
            findings.append("repeat run %d template_lock is malformed" % index)
        keys.append({
            "source_generation": source_generation,
            "template_lock": template_lock,
            "candidate_sha256": candidate,
            "gate_result_sha256": gate_hash,
        })
    consistent = bool(keys) and all(key == keys[0] for key in keys[1:]) and not findings
    status = "PASS" if consistent else (M3_FAIL_REPEAT if len(keys) >= 2 and not findings else M3_BLOCKED_INPUT)
    return {
        "status": status,
        "repeat_consistent": consistent,
        "run_count": len(runs),
        "run_ids": sorted(run_ids),
        "comparison_keys": keys,
        "repeat_result_sha256": qualification_digest({"comparison_keys": keys, "run_ids": sorted(run_ids)}),
        "findings": sorted(set(findings)),
    }
