"""Machine-readable Xcelium L1 event evaluation owner."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from ..artifact_paths import PathContractError, path_has_symlink_component, validate_relative_path
from ..l1_matrix import validate_observed_matrix

def _evaluate_l1_events(
    plan: Mapping[str, Any], log_text: str, payload_root: Path | None = None
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(log_text.splitlines(), 1):
        marker = "AIVW_L1_EVENT "
        if marker not in line:
            continue
        raw = line.split(marker, 1)[1].strip()
        try:
            event = json.loads(raw, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            findings.append(
                {
                    "code": "malformed_event",
                    "line": line_number,
                    "detail": str(exc),
                }
            )
            continue
        if not isinstance(event, dict):
            findings.append(
                {"code": "malformed_event", "line": line_number, "detail": "event is not an object"}
            )
            continue
        events.append(event)

    raw_cases = plan.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        return {
            "schema_version": 1,
            "status": "BLOCKED_INPUT",
            "findings": [{"code": "invalid_test_plan", "detail": "cases are missing"}],
            "case_coverage": 0.0,
            "events": events,
        }
    expected_cases = {
        str(item.get("id")): item
        for item in raw_cases
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    case_events: dict[str, dict[str, Any]] = {}
    check_events: dict[str, dict[str, Any]] = {}
    temporal_events: dict[str, dict[str, Any]] = {}
    coverage_events: dict[str, dict[str, Any]] = {}
    waveform_events: dict[str, dict[str, Any]] = {}
    summaries: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("event")
        if event_type == "summary":
            summaries.append(event)
            continue
        event_id = event.get("id")
        if not isinstance(event_id, str):
            findings.append({"code": "event_id_missing", "event": event})
            continue
        target = (
            case_events
            if event_type == "case"
            else check_events
            if event_type == "check"
            else temporal_events
            if event_type == "temporal"
            else coverage_events
            if event_type == "coverage"
            else waveform_events
            if event_type == "waveform"
            else None
        )
        if target is None:
            findings.append({"code": "unknown_event_type", "event": event})
            continue
        if event_id in target:
            findings.append({"code": "duplicate_event", "event_type": event_type, "id": event_id})
            continue
        target[event_id] = event

    expected_case_ids = set(expected_cases)
    actual_case_ids = set(case_events)
    for case_id in sorted(expected_case_ids - actual_case_ids):
        findings.append({"code": "missing_case", "id": case_id})
    for case_id in sorted(actual_case_ids - expected_case_ids):
        findings.append({"code": "extra_case", "id": case_id})
    if expected_case_ids != actual_case_ids and plan.get("matrix") is not None:
        findings.append(
            {
                "code": "matrix_case_set_mismatch",
                "expected": sorted(expected_case_ids),
                "actual": sorted(actual_case_ids),
            }
        )
    matrix_summary = None
    raw_matrix = plan.get("matrix")
    if raw_matrix is not None:
        if not isinstance(raw_matrix, Mapping):
            findings.append({"code": "matrix_contract_invalid"})
        else:
            matrix_summary, matrix_findings = validate_observed_matrix(
                raw_matrix,
                [
                    {
                        "id": case_id,
                        **{
                            key: case_events[case_id][key]
                            for key in ("corner", "vector_id")
                            if key in case_events[case_id]
                        },
                    }
                    for case_id in sorted(actual_case_ids & expected_case_ids)
                ],
            )
            findings.extend(matrix_findings)
    expected_checks = {
        str(value) for value in plan.get("required_checks", []) if isinstance(value, str)
    }
    for check_id in sorted(expected_checks - set(check_events)):
        findings.append({"code": "missing_check", "id": check_id})
    for check_id in sorted(set(check_events) - expected_checks):
        findings.append({"code": "extra_check", "id": check_id})
    for check_id in sorted(expected_checks & set(check_events)):
        if check_events[check_id].get("status") != "PASS":
            findings.append(
                {
                    "code": "check_not_pass",
                    "id": check_id,
                    "actual": check_events[check_id].get("status"),
                }
            )

    raw_temporal = plan.get("temporal_assertions", [])
    if raw_temporal is None:
        raw_temporal = []
    if not isinstance(raw_temporal, list):
        findings.append(
            {
                "code": "invalid_temporal_assertions",
                "detail": "temporal_assertions must be an array",
            }
        )
        expected_temporal: dict[str, Mapping[str, Any]] = {}
    else:
        expected_temporal = {
            str(item.get("id")): item
            for item in raw_temporal
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        }
        if len(expected_temporal) != len(raw_temporal):
            findings.append(
                {
                    "code": "invalid_temporal_assertions",
                    "detail": "each temporal assertion must have a unique string id",
                }
            )
    expected_temporal_ids = set(expected_temporal)
    actual_temporal_ids = set(temporal_events)
    for assertion_id in sorted(expected_temporal_ids - actual_temporal_ids):
        findings.append({"code": "missing_temporal_assertion", "id": assertion_id})
    for assertion_id in sorted(actual_temporal_ids - expected_temporal_ids):
        findings.append({"code": "extra_temporal_assertion", "id": assertion_id})
    for assertion_id in sorted(expected_temporal_ids & actual_temporal_ids):
        event = temporal_events[assertion_id]
        if event.get("status") != "PASS":
            findings.append(
                {
                    "code": "temporal_assertion_not_pass",
                    "id": assertion_id,
                    "actual": event.get("status"),
                }
            )

    expected_coverage = plan.get("coverage")
    if expected_coverage is not None:
        if not isinstance(expected_coverage, Mapping):
            findings.append({"code": "invalid_coverage_contract"})
        else:
            coverage_id = expected_coverage.get("id")
            if not isinstance(coverage_id, str):
                findings.append({"code": "invalid_coverage_contract"})
            elif coverage_id not in coverage_events:
                findings.append({"code": "missing_coverage", "id": coverage_id})
            else:
                event = coverage_events[coverage_id]
                if event.get("status") != "PASS":
                    findings.append({"code": "coverage_not_pass", "id": coverage_id})
                if event.get("backend") != expected_coverage.get("backend"):
                    findings.append({"code": "coverage_backend_mismatch", "id": coverage_id})
                score = event.get("score")
                minimum_score = expected_coverage.get("minimum_score")
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(float(score))
                    or not isinstance(minimum_score, (int, float))
                    or float(score) < float(minimum_score)
                ):
                    findings.append({"code": "coverage_below_minimum", "id": coverage_id, "actual": score, "expected": minimum_score})
                required_points = set(expected_coverage.get("required_points", []))
                observed_points = event.get("covered_points")
                observed_point_set = (
                    set(observed_points)
                    if isinstance(observed_points, list)
                    and all(isinstance(point, str) for point in observed_points)
                    else None
                )
                if observed_point_set is None or not required_points.issubset(
                    observed_point_set
                ):
                    findings.append({"code": "coverage_points_missing", "id": coverage_id, "expected": sorted(required_points), "actual": observed_points})
                _validate_evidence_path(
                    findings, event, coverage_id, "coverage", payload_root
                )
            extra_coverage = set(coverage_events) - {coverage_id}
            for extra_id in sorted(extra_coverage):
                findings.append({"code": "extra_coverage", "id": extra_id})
    elif coverage_events:
        for coverage_id in sorted(coverage_events):
            findings.append({"code": "extra_coverage", "id": coverage_id})

    expected_waveform = plan.get("waveform")
    if expected_waveform is not None:
        if not isinstance(expected_waveform, Mapping):
            findings.append({"code": "invalid_waveform_contract"})
        else:
            waveform_id = expected_waveform.get("id")
            retention = expected_waveform.get("retention")
            if not isinstance(waveform_id, str):
                findings.append({"code": "invalid_waveform_contract"})
            elif waveform_id not in waveform_events:
                # ``on_failure`` intentionally has no retained waveform on a
                # clean pass; ``never`` forbids one altogether.  ``always``
                # is the only policy that requires an event unconditionally.
                if retention == "always":
                    findings.append({"code": "missing_waveform", "id": waveform_id})
            else:
                event = waveform_events[waveform_id]
                if retention == "never":
                    findings.append({"code": "waveform_retained_when_forbidden", "id": waveform_id})
                elif event.get("status") != "PASS":
                    findings.append({"code": "waveform_not_pass", "id": waveform_id})
                if event.get("format") != expected_waveform.get("format"):
                    findings.append({"code": "waveform_format_mismatch", "id": waveform_id})
                _validate_evidence_path(
                    findings, event, waveform_id, "waveform", payload_root
                )
            extra_waveform = set(waveform_events) - {waveform_id}
            for extra_id in sorted(extra_waveform):
                findings.append({"code": "extra_waveform", "id": extra_id})
    elif waveform_events:
        for waveform_id in sorted(waveform_events):
            findings.append({"code": "extra_waveform", "id": waveform_id})

    required_metrics = {
        str(value) for value in plan.get("required_metrics", []) if isinstance(value, str)
    }
    assertion_count = 0
    for case_id in sorted(expected_case_ids & actual_case_ids):
        expected = expected_cases[case_id]
        actual = case_events[case_id]
        if actual.get("status") != "PASS":
            findings.append({"code": "case_not_pass", "id": case_id, "actual": actual.get("status")})
        for key in ("type", "region"):
            if actual.get(key) != expected.get(key):
                findings.append(
                    {
                        "code": "case_identity_mismatch",
                        "id": case_id,
                        "field": key,
                        "expected": expected.get(key),
                        "actual": actual.get(key),
                    }
                )
        for key in ("corner", "vector_id"):
            if key in expected or key in actual:
                if key in expected and key in actual and actual.get(key) == expected.get(key):
                    continue
                findings.append(
                    {
                        "code": "case_metadata_mismatch",
                        "id": case_id,
                        "field": key,
                        "expected": expected.get(key),
                        "actual": actual.get(key),
                    }
                )
        expected_assertions = set(expected.get("required_assertions", []))
        actual_assertions = actual.get("passed_assertions")
        if not isinstance(actual_assertions, list) or set(actual_assertions) != expected_assertions:
            findings.append(
                {
                    "code": "assertion_set_mismatch",
                    "id": case_id,
                    "expected": sorted(expected_assertions),
                    "actual": actual_assertions,
                }
            )
        else:
            assertion_count += len(actual_assertions)
        metrics = actual.get("metrics")
        expected_metrics = expected.get("metrics")
        if not isinstance(metrics, Mapping) or not isinstance(expected_metrics, Mapping):
            findings.append({"code": "metric_set_missing", "id": case_id})
            continue
        expected_names = set(expected_metrics) & required_metrics
        actual_names = set(metrics)
        if actual_names != expected_names:
            findings.append(
                {
                    "code": "metric_set_mismatch",
                    "id": case_id,
                    "expected": sorted(expected_names),
                    "actual": sorted(str(name) for name in actual_names),
                }
            )
            continue
        for name in sorted(expected_names):
            definition = expected_metrics[name]
            value = metrics[name]
            kind = definition.get("kind") if isinstance(definition, Mapping) else None
            expected_value = definition.get("expected") if isinstance(definition, Mapping) else None
            if kind == "numeric":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    findings.append({"code": "metric_non_finite", "id": case_id, "metric": name, "actual": value})
                    continue
                tolerance = float(definition.get("absolute_tolerance", 0.0))
                if abs(float(value) - float(expected_value)) > tolerance:
                    findings.append(
                        {
                            "code": "metric_out_of_tolerance",
                            "id": case_id,
                            "metric": name,
                            "expected": expected_value,
                            "actual": value,
                            "absolute_tolerance": tolerance,
                        }
                    )
            elif value != expected_value:
                findings.append(
                    {
                        "code": "metric_mismatch",
                        "id": case_id,
                        "metric": name,
                        "expected": expected_value,
                        "actual": value,
                    }
                )

    if len(summaries) != 1:
        findings.append({"code": "summary_count_mismatch", "actual": len(summaries)})
    else:
        summary = summaries[0]
        expected_assertion_count = sum(
            len(item.get("required_assertions", []))
            for item in expected_cases.values()
        )
        expected_summary = {
            "status": "PASS",
            "case_count": len(expected_cases),
            "check_count": len(expected_checks),
            "assertion_count": expected_assertion_count,
        }
        if expected_temporal_ids:
            expected_summary["temporal_count"] = len(expected_temporal_ids)
        if isinstance(expected_coverage, Mapping):
            expected_summary["coverage_count"] = 1
        if isinstance(expected_waveform, Mapping):
            expected_summary["waveform_count"] = (
                1 if expected_waveform.get("retention") == "always" else 0
            )
        if isinstance(raw_matrix, Mapping):
            expected_summary["matrix_point_count"] = (
                matrix_summary.get("expected_point_count")
                if isinstance(matrix_summary, Mapping)
                else raw_matrix.get("expected_point_count")
            )
        for key, expected_value in expected_summary.items():
            if summary.get(key) != expected_value:
                findings.append(
                    {
                        "code": "summary_mismatch",
                        "field": key,
                        "expected": expected_value,
                        "actual": summary.get(key),
                    }
                )
    coverage = len(expected_case_ids & actual_case_ids) / len(expected_case_ids)
    minimum = plan.get("minimum_case_coverage")
    if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and coverage < float(minimum):
        findings.append(
            {"code": "case_coverage_below_minimum", "expected": minimum, "actual": coverage}
        )
    if isinstance(expected_waveform, Mapping):
        waveform_id = expected_waveform.get("id")
        retention = expected_waveform.get("retention")
        if retention == "on_failure" and isinstance(waveform_id, str):
            if waveform_id in waveform_events and not findings:
                findings.append(
                    {"code": "waveform_retained_on_pass", "id": waveform_id}
                )
    return {
        "schema_version": 1,
        "status": "PASS" if not findings else "FAIL",
        "protocol": plan.get("protocol"),
        "expected_case_ids": sorted(expected_case_ids),
        "observed_case_ids": sorted(actual_case_ids),
        "required_checks": sorted(expected_checks),
        "observed_checks": sorted(check_events),
        "required_metrics": sorted(required_metrics),
        "required_temporal_assertions": sorted(expected_temporal_ids),
        "observed_temporal_assertions": sorted(actual_temporal_ids),
        "temporal_assertion_count": len(actual_temporal_ids),
        "required_coverage_id": expected_coverage.get("id") if isinstance(expected_coverage, Mapping) else None,
        "observed_coverage_ids": sorted(coverage_events),
        "required_waveform_id": expected_waveform.get("id") if isinstance(expected_waveform, Mapping) else None,
        "observed_waveform_ids": sorted(waveform_events),
        "waveform": dict(expected_waveform) if isinstance(expected_waveform, Mapping) else None,
        "case_coverage": coverage,
        "passed_assertion_count": assertion_count,
        "excluded_metrics": plan.get("excluded_metrics", []),
        "matrix": matrix_summary,
        "findings": findings,
        "events": events,
    }

def _validate_evidence_path(
    findings: list[dict[str, Any]],
    event: Mapping[str, Any],
    identifier: str,
    kind: str,
    payload_root: Path | None,
) -> None:
    path_value = event.get("path")
    try:
        validate_relative_path(path_value, "evidence")
    except PathContractError:
        findings.append(
            {"code": f"{kind}_path_invalid", "id": identifier, "actual": path_value}
        )
        return
    if payload_root is None:
        return
    raw_candidate = payload_root / Path(path_value)
    if path_has_symlink_component(payload_root, raw_candidate):
        findings.append(
            {
                "code": f"{kind}_artifact_symlink",
                "id": identifier,
                "path": path_value,
            }
        )
        return
    candidate = raw_candidate.resolve()
    if (
        not candidate.is_relative_to(payload_root.resolve())
        or not candidate.exists()
        or not (candidate.is_file() or candidate.is_dir())
    ):
        findings.append(
            {
                "code": f"{kind}_artifact_missing",
                "id": identifier,
                "path": path_value,
            }
        )

def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")
