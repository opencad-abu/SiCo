"""Deterministic comparator parameter-to-instance routing."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from ..l1_matrix import normalize_l1_matrix
from .xcelium_evidence_contract import ADAPTER_NAME


_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


QUALIFIED_MODEL_PARAMETERS = {
    "vdd_high": "VDD_HIGH",
    "vss_low": "VSS_LOW",
    "input_offset": "INPUT_OFFSET",
    "decision_epsilon": "DECISION_EPSILON",
}


def sv_real_number(value: object, label: str) -> float:
    """Convert a model parameter to an unambiguous finite SV ``real`` value.

    Matrix coordinates are canonicalized without a floating-point round trip,
    but the comparator adapter ultimately emits a SystemVerilog real
    parameter override.  Reject integers that cannot be represented by that
    override without changing their value; silently rounding them could merge
    two distinct matrix points into one DUT instance.  The caller separately
    checks for collisions between distinct declared values after conversion.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be finite numeric")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be finite numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite numeric")
    if isinstance(value, int) and int(result) != value:
        raise ValueError(f"{label} cannot be represented exactly as SV real")
    return result


def qualified_model_parameter_mapping() -> dict[str, str]:
    """Return the comparator adapter's finite, recipe-name-to-SV map.

    The mapping is deliberately owned by this model-class adapter.  A recipe
    may describe a ``model_parameter`` matrix dimension only when its name is
    present here; generic L1 code must not infer a DUT parameter spelling.
    """
    return dict(QUALIFIED_MODEL_PARAMETERS)


def qualified_model_parameter_routes(
    plan: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Build deterministic comparator case-to-DUT instance routes.

    Only registered ``model_parameter`` dimensions participate in instance
    construction. Context and stimulus dimensions remain auditable metadata;
    they never become simulator parameter overrides.
    """
    if not isinstance(plan, Mapping):
        raise ValueError("plan must be an object")
    matrix = plan.get("matrix")
    if matrix is None:
        return None
    if not isinstance(matrix, Mapping):
        raise ValueError("matrix contract must be an object")
    raw_cases = plan.get("cases")
    if (
        not isinstance(raw_cases, list)
        or not raw_cases
        or any(not isinstance(item, Mapping) for item in raw_cases)
    ):
        raise ValueError("matrix cases must be a non-empty array of objects")
    cases = list(raw_cases)
    normalized = normalize_l1_matrix(matrix, cases)
    model_dimensions = [
        item
        for item in normalized["dimensions"]
        if item["application"] == "model_parameter"
    ]
    if not model_dimensions:
        return None

    dimension_bindings: list[dict[str, str]] = []
    for dimension in model_dimensions:
        name = str(dimension["name"])
        sv_parameter = QUALIFIED_MODEL_PARAMETERS.get(name)
        if sv_parameter is None:
            raise ValueError(f"matrix model parameter is not qualified: {name}")
        values = dimension["values"]
        converted_values = {
            sv_real_number(value, f"matrix model parameter {name}")
            for value in values
        }
        if len(converted_values) != len(values):
            raise ValueError(
                f"matrix model parameter values collapse after SV real conversion: {name}"
            )
        dimension_bindings.append({"name": name, "sv_parameter": sv_parameter})

    instances: list[dict[str, Any]] = []
    by_coordinate: dict[tuple[str, ...], dict[str, Any]] = {}
    case_keys: dict[str, str] = {}
    case_instances: dict[str, str] = {}
    case_routes: dict[str, dict[str, Any]] = {}
    seen_case_ids: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if (
            not isinstance(case_id, str)
            or not _SAFE_ID.fullmatch(case_id)
            or case_id in seen_case_ids
        ):
            raise ValueError("matrix cases must define unique safe IDs")
        seen_case_ids.add(case_id)
        parameters: dict[str, float] = {}
        overrides: dict[str, float] = {}
        coordinate: list[str] = []
        for dimension, binding in zip(model_dimensions, dimension_bindings):
            if dimension["source"] == "vector_id":
                raw_value = case.get("vector_id")
            else:
                corner = case.get("corner")
                raw_value = (
                    corner.get(dimension["name"])
                    if isinstance(corner, Mapping)
                    else None
                )
            value = sv_real_number(
                raw_value,
                f"case {case_id} matrix parameter {dimension['name']}",
            )
            coordinate.append(format(value, ".17g"))
            parameters[binding["name"]] = value
            overrides[binding["sv_parameter"]] = value
        coordinate_key = tuple(coordinate)
        instance = by_coordinate.get(coordinate_key)
        if instance is None:
            suffix = f"{len(instances):04d}"
            instance = {
                "key": "|".join(coordinate_key),
                "suffix": suffix,
                "name": f"dut_matrix_{suffix}",
                "parameters": parameters,
                "overrides": overrides,
                "case_ids": [],
            }
            by_coordinate[coordinate_key] = instance
            instances.append(instance)
        instance["case_ids"].append(case_id)
        case_keys[case_id] = instance["key"]
        case_instances[case_id] = instance["name"]
        case_routes[case_id] = {
            "adapter": ADAPTER_NAME,
            "instance": instance["name"],
            "parameters": dict(instance["parameters"]),
            "sv_overrides": dict(instance["overrides"]),
        }

    return {
        "dimensions": dimension_bindings,
        "instances": instances,
        "case_keys": case_keys,
        "case_instances": case_instances,
        "case_routes": case_routes,
        "case_overrides": {
            instance["key"]: dict(instance["overrides"])
            for instance in instances
        },
        "instance_count": len(instances),
    }
