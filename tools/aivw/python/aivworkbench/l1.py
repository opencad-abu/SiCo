"""Deterministic recipe-to-L1 test-plan normalization."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .l1_matrix import normalize_l1_matrix
from .pvt_contract import normalize_physical_pvt_matrix


def build_l1_test_plan(verification: Mapping[str, Any]) -> dict[str, Any]:
    """Build the authoritative L1 plan from recipe-owned verification data."""
    l1 = verification.get("l1")
    raw_cases = verification.get("cases")
    raw_metrics = verification.get("metrics")
    if not isinstance(l1, Mapping) or not isinstance(raw_cases, list):
        raise ValueError("recipe verification must define l1 policy and cases")
    if not isinstance(raw_metrics, list):
        raise ValueError("recipe verification must define metrics")
    metric_definitions = {
        str(item["name"]): item
        for item in raw_metrics
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    required_metrics = [
        event_id(value, "required metric")
        for value in l1.get("required_metrics", [])
    ]
    cases: list[dict[str, Any]] = []
    for raw in raw_cases:
        if not isinstance(raw, Mapping):
            raise ValueError("verification case must be an object")
        case_id = event_id(raw.get("id"), "case id")
        case_type = event_id(raw.get("type"), f"case {case_id} type")
        inputs = raw.get("inputs")
        expected = raw.get("expected")
        assertions = raw.get("assertions")
        if not isinstance(inputs, Mapping) or not isinstance(expected, Mapping):
            raise ValueError(
                f"verification case {case_id} requires inputs and expected metrics"
            )
        if set(expected) != set(required_metrics):
            raise ValueError(
                f"verification case {case_id} expected metrics must equal required_metrics"
            )
        if not isinstance(assertions, list) or not assertions:
            raise ValueError(f"verification case {case_id} requires assertions")
        metric_plan: dict[str, dict[str, Any]] = {}
        for name, value in expected.items():
            definition = metric_definitions.get(str(name))
            if definition is None:
                raise ValueError(
                    f"verification case {case_id} references unknown metric {name!r}"
                )
            kind = str(definition.get("kind", ""))
            metric: dict[str, Any] = {"kind": kind, "expected": value}
            if "unit" in definition:
                metric["unit"] = definition["unit"]
            if "absolute_tolerance" in definition:
                metric["absolute_tolerance"] = definition["absolute_tolerance"]
            metric_plan[str(name)] = metric
        cases.append(
            {
                "id": case_id,
                "type": case_type,
                "region": str(raw.get("region", "deterministic")),
                "inputs": dict(inputs),
                "metrics": metric_plan,
                "required_assertions": [
                    event_id(value, f"case {case_id} assertion")
                    for value in assertions
                ],
            }
        )
        if "corner" in raw:
            if not isinstance(raw["corner"], Mapping):
                raise ValueError(f"case {case_id} corner must be an object")
            cases[-1]["corner"] = dict(raw["corner"])
        if "vector_id" in raw:
            cases[-1]["vector_id"] = event_id(
                raw["vector_id"], f"case {case_id} vector_id"
            )
    excluded = [
        {"name": str(item["name"]), "reason": "calibration_required"}
        for item in raw_metrics
        if isinstance(item, Mapping) and item.get("calibration_required") is True
    ]
    temporal_assertions = _temporal_assertions(l1.get("temporal_assertions", []))
    plan: dict[str, Any] = {
        "schema_version": 1,
        "protocol": str(l1.get("protocol", "")),
        "require_exact_case_set": l1.get("require_exact_case_set") is True,
        "require_exact_metric_set": l1.get("require_exact_metric_set") is True,
        "require_assertion_pass": l1.get("require_assertion_pass") is True,
        "minimum_case_coverage": l1.get("minimum_case_coverage"),
        "required_metrics": required_metrics,
        "required_checks": [
            event_id(value, "required check")
            for value in l1.get("required_checks", [])
        ],
        "hold_case": event_id(l1.get("hold_case"), "hold case"),
        "cases": cases,
        "excluded_metrics": excluded,
        "temporal_assertions": temporal_assertions,
    }
    if "matrix" in l1:
        plan["matrix"] = normalize_l1_matrix(l1["matrix"], cases)
    if "coverage" in l1:
        plan["coverage"] = _coverage_contract(l1["coverage"])
    if "waveform" in l1:
        plan["waveform"] = _waveform_contract(l1["waveform"])
    if "physical_pvt" in verification:
        plan["physical_pvt"] = normalize_physical_pvt_matrix(
            verification["physical_pvt"], cases
        )
    return plan


def _temporal_assertions(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("temporal_assertions must be an array")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("temporal assertion must be an object")
        identifier = event_id(raw.get("id"), "temporal assertion id")
        if identifier in seen:
            raise ValueError("temporal assertion IDs must be unique")
        seen.add(identifier)
        property_text = raw.get("property")
        clock = raw.get("clock")
        if not isinstance(property_text, str) or not property_text.strip():
            raise ValueError(f"temporal assertion {identifier} property must be text")
        if not isinstance(clock, str) or not clock.strip():
            raise ValueError(f"temporal assertion {identifier} clock must be text")
        item: dict[str, Any] = {
            "id": identifier,
            "property": property_text,
            "clock": clock,
        }
        if "severity" in raw:
            severity = raw["severity"]
            if not isinstance(severity, str) or not severity.strip():
                raise ValueError(f"temporal assertion {identifier} severity must be text")
            item["severity"] = severity
        result.append(item)
    return result


def _coverage_contract(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("coverage contract must be an object")
    identifier = event_id(value.get("id"), "coverage id")
    backend = value.get("backend")
    if backend not in {"ucis", "imc"}:
        raise ValueError("coverage backend must be ucis or imc")
    minimum = value.get("minimum_score")
    if (
        isinstance(minimum, bool)
        or not isinstance(minimum, (int, float))
        or not 0.0 <= float(minimum) <= 1.0
    ):
        raise ValueError("coverage minimum_score must be between 0 and 1")
    required_points = value.get("required_points", [])
    if not isinstance(required_points, list) or any(
        not isinstance(point, str) or not point.strip() for point in required_points
    ):
        raise ValueError("coverage required_points must contain non-empty strings")
    if len(required_points) != len(set(required_points)):
        raise ValueError("coverage required_points must be unique")
    return {
        "id": identifier,
        "backend": backend,
        "minimum_score": float(minimum),
        "required_points": list(required_points),
    }


def _waveform_contract(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("waveform contract must be an object")
    identifier = event_id(value.get("id"), "waveform id")
    waveform_format = value.get("format")
    retention = value.get("retention")
    if waveform_format not in {"shm", "vcd", "fsdb"}:
        raise ValueError("waveform format must be shm, vcd, or fsdb")
    if retention not in {"always", "on_failure", "never"}:
        raise ValueError("waveform retention must be always, on_failure, or never")
    return {"id": identifier, "format": waveform_format, "retention": retention}


def event_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError(f"{label} must be an event-safe identifier")
    return value


__all__ = ["build_l1_test_plan", "event_id"]
