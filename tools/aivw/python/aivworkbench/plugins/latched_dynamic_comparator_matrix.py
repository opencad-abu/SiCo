"""Numeric and matrix routing helpers for comparator L1 rendering."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping

from ..l1_matrix import normalize_l1_matrix
from .xcelium_evidence import qualified_model_parameter_routes


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _matrix_instances(
    test_plan: Mapping[str, Any], parameters: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Build one controlled DUT instance per unique model-parameter tuple.

    Context dimensions and stimulus vectors remain case metadata.  Only the
    adapter's registered ``model_parameter`` names can become SystemVerilog
    parameter overrides, and every override is checked as a finite number.
    """
    matrix = test_plan.get("matrix")
    if matrix is None:
        return None
    if not isinstance(matrix, Mapping):
        raise ValueError("matrix contract must be an object")
    normalize_l1_matrix(matrix, test_plan.get("cases", []))
    routes = qualified_model_parameter_routes(test_plan)
    if routes is None:
        return None
    for dimension in routes["dimensions"]:
        name = dimension["name"]
        definition = parameters.get(name)
        if not isinstance(definition, Mapping) or "value" not in definition:
            raise ValueError(f"matrix model parameter is missing from spec: {name}")
        _finite_number(definition["value"], f"spec matrix parameter {name}")
    # Keep the original renderer helper's compact instance shape stable while
    # exposing the richer route table to the evidence adapter itself.
    return {
        **routes,
        "instances": [
            {
                "key": instance["key"],
                "suffix": instance["suffix"],
                "name": instance["name"],
                "overrides": dict(instance["overrides"]),
            }
            for instance in routes["instances"]
        ],
    }


def _case_supply_values(
    case: Mapping[str, Any],
    matrix_instances: Mapping[str, Any] | None,
    parameters: Mapping[str, Any],
) -> tuple[float, float]:
    """Resolve the effective rails used by a case's complementary assertion."""
    vdd = _finite_number(parameters["vdd_high"]["value"], "vdd_high")
    vss = _finite_number(parameters["vss_low"]["value"], "vss_low")
    if matrix_instances is None:
        return vdd, vss
    case_id = str(case.get("id", ""))
    key = matrix_instances["case_keys"].get(case_id)
    overrides = matrix_instances.get("case_overrides", {}).get(key, {})
    if "VDD_HIGH" in overrides:
        vdd = _finite_number(overrides["VDD_HIGH"], f"case {case_id} VDD_HIGH")
    if "VSS_LOW" in overrides:
        vss = _finite_number(overrides["VSS_LOW"], f"case {case_id} VSS_LOW")
    return vdd, vss


def _sv_event(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sv_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


__all__ = ["_finite_number", "_matrix_instances", "_case_supply_values", "_sv_event", "_sv_string"]
