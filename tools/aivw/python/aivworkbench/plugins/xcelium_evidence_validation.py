"""Validate comparator evidence bindings against the canonical L1 plan."""

from __future__ import annotations

from typing import Any, Mapping

from ..l1_matrix import normalize_l1_matrix
from .xcelium_evidence_contract import ADAPTER_NAME
from .xcelium_evidence_routes import (
    QUALIFIED_MODEL_PARAMETERS, _SAFE_ID, sv_real_number,
    qualified_model_parameter_routes,
)


def binding_for_plan(plan: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    binding: dict[str, dict[str, str]] = {}
    coverage = plan.get("coverage")
    if isinstance(coverage, Mapping):
        binding["coverage"] = {
            "id": str(coverage.get("id", "")),
            "path": "evidence/coverage",
            "producer": "xcelium",
        }
    waveform = plan.get("waveform")
    if isinstance(waveform, Mapping):
        binding["waveform"] = {
            "id": str(waveform.get("id", "")),
            "path": "evidence/waves.shm",
            "producer": "xcelium",
        }
    return binding


def validate_plan(
    plan: Mapping[str, Any],
    binding: Mapping[str, Mapping[str, Any]],
    *, adapter_name: str = ADAPTER_NAME,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not isinstance(plan, Mapping):
        return [
            {
                "code": "xcelium_evidence_plan_invalid",
                "detail": "adapter plan must be an object",
            }
        ]
    if not isinstance(binding, Mapping):
        return [
            {
                "code": "xcelium_evidence_binding_invalid",
                "detail": "adapter binding must be an object",
            }
        ]
    if plan.get("coverage") is None and plan.get("waveform") is None:
        findings.append(
            {
                "code": "xcelium_evidence_adapter_has_no_evidence_contract",
                "adapter": adapter_name,
            }
        )
    required_binding_types = {
        kind for kind in ("coverage", "waveform") if plan.get(kind) is not None
    }
    missing_binding_types = required_binding_types - set(binding)
    for kind in sorted(missing_binding_types):
        findings.append(
            {"code": "xcelium_evidence_binding_missing", "type": kind}
        )
    extra_binding_types = set(binding) - required_binding_types
    for kind in sorted(extra_binding_types):
        findings.append(
            {"code": "xcelium_evidence_binding_extra_type", "type": kind}
        )
    temporal = plan.get("temporal_assertions", [])
    if not isinstance(temporal, list):
        findings.append(
            {
                "code": "xcelium_evidence_temporal_contract_invalid",
                "detail": "temporal_assertions must be an array",
            }
        )
    elif temporal:
        findings.append(
            {
                "code": "xcelium_evidence_temporal_unqualified",
                "adapter": adapter_name,
            }
        )
    matrix = plan.get("matrix")
    if matrix is not None and not isinstance(matrix, Mapping):
        findings.append(
            {
                "code": "xcelium_evidence_matrix_invalid",
                "detail": "matrix contract must be an object",
                "adapter": adapter_name,
            }
        )
    elif isinstance(matrix, Mapping):
        try:
            normalize_l1_matrix(
                matrix,
                [
                    item
                    for item in plan.get("cases", [])
                    if isinstance(item, Mapping)
                ],
            )
        except (TypeError, ValueError) as exc:
            findings.append(
                {
                    "code": "xcelium_evidence_matrix_invalid",
                    "detail": str(exc),
                    "adapter": adapter_name,
                }
            )
        else:
            for dimension in matrix.get("dimensions", []):
                if not isinstance(dimension, Mapping):
                    continue
                if dimension.get("application") != "model_parameter":
                    continue
                name = dimension.get("name")
                if name not in QUALIFIED_MODEL_PARAMETERS:
                    findings.append(
                        {
                            "code": "xcelium_evidence_matrix_model_parameter_unqualified",
                            "dimension": name,
                            "adapter": adapter_name,
                        }
                    )
                    continue
                values = dimension.get("values")
                numeric_values = True
                if isinstance(values, list):
                    for value in values:
                        try:
                            sv_real_number(
                                value,
                                f"matrix model parameter {name}",
                            )
                        except ValueError:
                            numeric_values = False
                            break
                else:
                    numeric_values = False
                if not numeric_values:
                    findings.append(
                        {
                            "code": "xcelium_evidence_matrix_model_parameter_non_numeric",
                            "dimension": name,
                            "adapter": adapter_name,
                        }
                    )
    # Route generation is model-class-owned, but direct adapter callers
    # still receive the same fail-closed contract as the recipe path.
    if matrix is not None and isinstance(matrix, Mapping):
        try:
            qualified_model_parameter_routes(plan)
        except (TypeError, ValueError) as exc:
            if not any(
                item.get("code") == "xcelium_evidence_matrix_invalid"
                for item in findings
            ):
                findings.append(
                    {
                        "code": "xcelium_evidence_matrix_invalid",
                        "detail": str(exc),
                        "adapter": adapter_name,
                    }
                )
    coverage = plan.get("coverage")
    if isinstance(coverage, Mapping):
        coverage_id = coverage.get("id")
        if not isinstance(coverage_id, str) or not _SAFE_ID.fullmatch(coverage_id):
            findings.append(
                {
                    "code": "xcelium_evidence_coverage_id_invalid",
                    "actual": coverage_id,
                }
            )
        if coverage.get("backend") not in {"ucis", "imc"}:
            findings.append(
                {
                    "code": "xcelium_evidence_coverage_backend_unqualified",
                    "expected": ["imc", "ucis"],
                    "actual": coverage.get("backend"),
                }
            )
        item = binding.get("coverage")
        if not isinstance(item, Mapping):
            findings.append(
                {
                    "code": "xcelium_evidence_coverage_binding_missing",
                }
            )
        else:
            validate_binding_item(
                findings,
                "coverage",
                item,
                coverage_id,
                "evidence/coverage",
            )
    waveform = plan.get("waveform")
    if isinstance(waveform, Mapping):
        waveform_id = waveform.get("id")
        if not isinstance(waveform_id, str) or not _SAFE_ID.fullmatch(waveform_id):
            findings.append(
                {
                    "code": "xcelium_evidence_waveform_id_invalid",
                    "actual": waveform_id,
                }
            )
        if waveform.get("format") != "shm":
            findings.append(
                {
                    "code": "xcelium_evidence_waveform_format_unqualified",
                    "expected": "shm",
                    "actual": waveform.get("format"),
                }
            )
        if waveform.get("retention") not in {"always", "on_failure", "never"}:
            findings.append(
                {
                    "code": "xcelium_evidence_waveform_retention_invalid",
                    "actual": waveform.get("retention"),
                }
            )
        item = binding.get("waveform")
        if not isinstance(item, Mapping):
            findings.append(
                {
                    "code": "xcelium_evidence_waveform_binding_missing",
                }
            )
        else:
            validate_binding_item(
                findings,
                "waveform",
                item,
                waveform_id,
                "evidence/waves.shm",
            )
    try:
        known_points = point_names(plan)
    except (TypeError, ValueError):
        known_points = []
        findings.append(
            {
                "code": "xcelium_evidence_l1_points_invalid",
                "detail": "required_checks and cases must define safe identifiers",
            }
        )
    if isinstance(coverage, Mapping):
        required_points = coverage.get("required_points", [])
        unique_points = False
        if isinstance(required_points, list):
            try:
                unique_points = len(required_points) == len(set(required_points))
            except TypeError:
                unique_points = False
        if (
            not isinstance(required_points, list)
            or not unique_points
            or any(
                not isinstance(point, str) or point not in known_points
                for point in required_points
            )
        ):
            findings.append(
                {
                    "code": "xcelium_evidence_coverage_point_unqualified",
                    "known_points": known_points,
                    "actual": required_points,
                }
            )
    return findings


def validate_binding_item(
    findings: list[dict[str, Any]],
    kind: str,
    item: Mapping[str, Any],
    expected_id: object,
    expected_path: str,
) -> None:
    expected_fields = {"id", "path", "producer"}
    extra_fields = set(item) - expected_fields
    if extra_fields:
        findings.append(
            {
                "code": "xcelium_evidence_binding_extra_field",
                "type": kind,
                "fields": sorted(str(value) for value in extra_fields),
            }
        )
    if item.get("id") != expected_id:
        findings.append(
            {
                "code": "xcelium_evidence_binding_id_mismatch",
                "type": kind,
                "expected": expected_id,
                "actual": item.get("id"),
            }
        )
    if item.get("path") != expected_path:
        findings.append(
            {
                "code": "xcelium_evidence_binding_path_mismatch",
                "type": kind,
                "expected": expected_path,
                "actual": item.get("path"),
            }
        )
    if item.get("producer") != "xcelium":
        findings.append(
            {
                "code": "xcelium_evidence_binding_producer_invalid",
                "type": kind,
                "expected": "xcelium",
                "actual": item.get("producer"),
            }
        )


def point_names(plan: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for value in plan.get("required_checks", []):
        if isinstance(value, str) and value not in names:
            names.append(value)
    for case in plan.get("cases", []):
        if isinstance(case, Mapping) and isinstance(case.get("id"), str):
            if case["id"] not in names:
                names.append(case["id"])
    if not names:
        raise ValueError("Xcelium evidence adapter requires at least one L1 point")
    if any(not _SAFE_ID.fullmatch(value) for value in names):
        raise ValueError("Xcelium evidence point ID is not safe")
    return names
