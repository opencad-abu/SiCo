"""Validate exact physical PVT raw evidence without assigning a design verdict."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifact_paths import path_has_symlink_component
from .pvt_raw_output import raw_output_kind as output_kind, matches_raw_output_kind, raw_output_is_empty
from .pvt_paths import (
    diagnostic_relative_path,
    resolve_observed_path,
)
from .pvt_contract_request import normalize_contract_with_optional_points

_CONTROL_CHARS = frozenset("\x00\r\n;|&`$\\")
_SAFE_PRODUCER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*@[A-Za-z0-9_.+-]+$")

def evaluate_physical_pvt_results(
    contract: object,
    observations: Sequence[Mapping[str, Any]],
    *,
    payload_root: Path,
    producer: str,
    cases: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate exact raw output coverage for every declared matrix point.

    This evaluator intentionally stops at raw evidence.  It does not interpret
    analog metrics or turn simulator ``PASS`` records into a design verdict.
    A caller must add a separately approved metric/correlation evaluator before
    using the evidence for a physical qualification decision.
    """

    try:
        normalized = normalize_contract_with_optional_points(contract, cases)
    except (TypeError, ValueError) as exc:
        return _evidence_result("BLOCKED_INPUT", "pvt_contract_invalid", str(exc))
    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
        return _evidence_result(
            "BLOCKED_EVIDENCE", "pvt_observations_invalid", "observations must be an array"
        )
    if not isinstance(payload_root, Path) or not payload_root.is_absolute():
        return _evidence_result(
            "BLOCKED_EVIDENCE", "pvt_payload_root_invalid", "payload_root must be absolute"
        )
    if (
        not isinstance(producer, str)
        or not producer.strip()
        or any(character in _CONTROL_CHARS for character in producer)
        or any(character.isspace() for character in producer)
        or not _SAFE_PRODUCER.fullmatch(producer)
    ):
        return _evidence_result(
            "BLOCKED_EVIDENCE", "pvt_producer_invalid", "producer must be non-empty text"
        )
    try:
        payload_available = payload_root.is_dir() and not payload_root.is_symlink()
    except (OSError, RuntimeError):
        payload_available = False
    if not payload_available:
        return _evidence_result(
            "BLOCKED_EVIDENCE",
            "pvt_payload_root_unavailable",
            "payload_root must be an existing regular directory",
        )
    if path_has_symlink_component(payload_root, payload_root):
        return _evidence_result(
            "BLOCKED_EVIDENCE",
            "pvt_payload_root_unsafe",
            "payload_root contains a symlink component",
        )
    expected = {str(point["id"]): point for point in normalized["points"]}
    try:
        json.dumps(dict(normalized), allow_nan=False)
    except (TypeError, ValueError) as exc:
        return _evidence_result(
            "BLOCKED_INPUT", "pvt_contract_non_json", str(exc)
        )
    seen: set[str] = set()
    findings: list[dict[str, Any]] = []
    locators: list[dict[str, Any]] = []
    point_records: list[dict[str, Any]] = []
    for raw in observations:
        if not isinstance(raw, Mapping):
            findings.append({"code": "pvt_observation_invalid"})
            continue
        point_id = raw.get("id")
        if not isinstance(point_id, str) or point_id not in expected:
            findings.append(
                {
                    "code": "pvt_observation_unknown_point",
                    "point_id": _json_safe_detail(point_id),
                }
            )
            continue
        if point_id in seen:
            findings.append({"code": "pvt_observation_duplicate_point", "point_id": point_id})
            continue
        seen.add(point_id)
        expected_path = payload_root / Path(
            normalized["execution"]["payload_relative_root"]
        ) / point_id / normalized["execution"]["raw_output"]
        raw_output = raw.get("raw_output")
        try:
            output_path = resolve_observed_path(payload_root, raw_output)
        except (TypeError, ValueError) as exc:
            finding_code = (
                "pvt_raw_output_missing_or_unsafe"
                if "symlink" in str(exc).lower()
                else "pvt_raw_output_path_invalid"
            )
            findings.append(
                {"code": finding_code, "point_id": point_id, "detail": str(exc)}
            )
            continue
        try:
            expected_canonical = expected_path.resolve(strict=False)
        except (OSError, RuntimeError):
            findings.append(
                {
                    "code": "pvt_raw_output_expected_path_unavailable",
                    "point_id": point_id,
                    "path": diagnostic_relative_path(payload_root, expected_path),
                }
            )
            continue
        if output_path != expected_canonical:
            findings.append(
                {
                    "code": "pvt_raw_output_path_mismatch",
                    "point_id": point_id,
                    "expected": diagnostic_relative_path(payload_root, expected_path),
                    "actual": diagnostic_relative_path(payload_root, output_path),
                }
            )
            continue
        raw_output_kind = output_kind(normalized["execution"]["raw_output"])
        if path_has_symlink_component(payload_root, output_path) or not matches_raw_output_kind(
            output_path, raw_output_kind
        ):
            findings.append(
                {
                    "code": "pvt_raw_output_missing_or_unsafe",
                    "point_id": point_id,
                    "path": diagnostic_relative_path(payload_root, output_path),
                }
            )
            continue
        status = raw.get("status")
        if status not in {"PASS", "FAIL"}:
            findings.append(
                {
                    "code": "pvt_point_status_invalid",
                    "point_id": point_id,
                    "status": _json_safe_detail(status),
                }
            )
        coordinates = raw.get("coordinates")
        expected_coordinates = expected[point_id]["coordinates"]
        if not isinstance(coordinates, Mapping) or dict(coordinates) != dict(
            expected_coordinates
        ):
            findings.append(
                {
                    "code": "pvt_point_coordinates_mismatch",
                    "point_id": point_id,
                    "expected": dict(expected_coordinates),
                    "actual": _json_safe_detail(coordinates),
                }
            )
        if raw.get("model_section") != expected[point_id]["model_section"]:
            findings.append(
                {
                    "code": "pvt_point_model_section_mismatch",
                    "point_id": point_id,
                    "expected": expected[point_id]["model_section"],
                    "actual": _json_safe_detail(raw.get("model_section")),
                }
            )
        metrics = raw.get("metrics", {})
        if not isinstance(metrics, Mapping):
            findings.append({"code": "pvt_point_metrics_invalid", "point_id": point_id})
        else:
            try:
                json.dumps(dict(metrics), allow_nan=False)
            except (TypeError, ValueError) as exc:
                findings.append(
                    {
                        "code": "pvt_point_metrics_non_finite",
                        "point_id": point_id,
                        "detail": str(exc),
                    }
                )
        # An empty placeholder cannot stand in for simulator evidence.  For
        # directory outputs require at least one member; file outputs require
        # a non-zero size.
        try:
            if raw_output_is_empty(output_path, raw_output_kind):
                findings.append(
                    {
                        "code": "pvt_raw_output_empty",
                        "point_id": point_id,
                        "path": diagnostic_relative_path(payload_root, output_path),
                    }
                )
        except (OSError, RuntimeError) as exc:
            findings.append(
                {
                    "code": "pvt_raw_output_unreadable",
                    "point_id": point_id,
                    "detail": str(exc),
                }
            )
        try:
            point_record = {
                "id": point_id,
                "coordinates": dict(coordinates)
                if isinstance(coordinates, Mapping)
                else {},
                "model_section": raw.get("model_section"),
                "status": status,
                "metrics": dict(metrics) if isinstance(metrics, Mapping) else {},
            }
            json.dumps(point_record, allow_nan=False)
        except (TypeError, ValueError) as exc:
            findings.append(
                {
                    "code": "pvt_point_record_non_json",
                    "point_id": point_id,
                    "detail": str(exc),
                }
            )
            # Do not let malformed evidence make the evaluator's own result
            # impossible to serialize.  The point remains observed, but no
            # locator or unsafe point record is emitted for it.
            continue
        point_records.append(
            {
                **point_record,
                "raw_output": diagnostic_relative_path(payload_root, output_path),
            }
        )
        locators.append(
            {
                "path": diagnostic_relative_path(payload_root, output_path),
                "kind": raw_output_kind,
                "exists": True,
                "producer": producer,
                "point_id": point_id,
                "raw_output": normalized["execution"]["raw_output"],
            }
        )
    missing = sorted(set(expected) - seen)
    if missing:
        findings.append({"code": "pvt_observation_missing_points", "point_ids": missing})
    failed_points = sorted(
        str(record["id"]) for record in point_records if record.get("status") != "PASS"
    )
    if failed_points:
        findings.append({"code": "pvt_point_execution_failed", "point_ids": failed_points})
    status = "PASS" if not findings else "BLOCKED_EVIDENCE"
    return {
        "status": status,
        "code": "pvt_raw_evidence_verified" if status == "PASS" else "pvt_raw_evidence_invalid",
        "physical_pvt_execution": "raw_evidence_observed",
        "design_verdict": "NOT_ESTABLISHED",
        "qualification_scope": "physical_pvt_raw_evidence_contract_only",
        "expected_point_count": len(expected),
        "observed_point_count": len(point_records),
        "points": sorted(point_records, key=lambda item: str(item["id"])),
        "findings": findings,
        "artifact_locators": sorted(locators, key=lambda item: str(item["path"])),
        "metrics_evaluation": "not_performed",
    }


def _json_safe_detail(value: object) -> object:
    """Keep diagnostic fields JSON-safe without exposing arbitrary objects."""

    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return f"<{type(value).__name__}>"
    return value
def _evidence_result(status: str, code: str, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "code": code,
        "reason": reason,
        "physical_pvt_execution": "not_invoked",
        "design_verdict": "NOT_ESTABLISHED",
        "qualification_scope": "physical_pvt_raw_evidence_contract_only",
        "findings": [],
        "artifact_locators": [],
    }


__all__ = ["evaluate_physical_pvt_results"]
