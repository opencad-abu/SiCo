"""Canonical L1 corner/vector matrix validation."""

from __future__ import annotations

from decimal import Decimal
from itertools import product
import json
import math
import re
from typing import Any, Mapping, Sequence


_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_SOURCES = {"corner", "vector_id"}
_APPLICATIONS = {"context", "model_parameter", "stimulus"}
_MAX_POINTS = 4096


def normalize_l1_matrix(
    value: object, cases: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Normalize and prove an exact recipe-owned matrix cross product."""
    if not isinstance(value, Mapping):
        raise ValueError("matrix contract must be an object")
    allowed_fields = {
        "kind",
        "require_exact_cross_product",
        "dimensions",
        "expected_point_count",
    }
    required_fields = allowed_fields - {"expected_point_count"}
    if set(value) - allowed_fields or not required_fields.issubset(value):
        raise ValueError("matrix contract fields are invalid")
    if value.get("kind") != "exact_cross_product":
        raise ValueError("matrix kind must be exact_cross_product")
    if value.get("require_exact_cross_product") is not True:
        raise ValueError("matrix must require an exact cross product")
    raw_dimensions = value.get("dimensions")
    if not isinstance(raw_dimensions, list) or not raw_dimensions:
        raise ValueError("matrix dimensions must be a non-empty array")

    dimensions: list[dict[str, Any]] = []
    names: set[str] = set()
    vector_dimensions = 0
    for raw in raw_dimensions:
        if not isinstance(raw, Mapping):
            raise ValueError("matrix dimension must be an object")
        if set(raw) != {"name", "source", "application", "values"}:
            raise ValueError("matrix dimension fields are invalid")
        name = raw.get("name")
        source = raw.get("source")
        application = raw.get("application")
        values = raw.get("values")
        if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
            raise ValueError("matrix dimension name must be event-safe text")
        if name in names:
            raise ValueError("matrix dimension names must be unique")
        names.add(name)
        if source not in _SOURCES:
            raise ValueError("matrix dimension source must be corner or vector_id")
        if application not in _APPLICATIONS:
            raise ValueError(
                "matrix dimension application must be context, model_parameter, or stimulus"
            )
        if source == "vector_id":
            vector_dimensions += 1
            if application != "stimulus":
                raise ValueError("vector_id matrix dimension must be a stimulus")
        elif application == "stimulus":
            raise ValueError("corner matrix dimension cannot be a stimulus")
        if not isinstance(values, list) or not values:
            raise ValueError(f"matrix dimension {name} values must be non-empty")
        normalized_values = [
            _scalar(item, f"matrix dimension {name}") for item in values
        ]
        keys = [_value_key(item) for item in normalized_values]
        if len(keys) != len(set(keys)):
            raise ValueError(f"matrix dimension {name} values must be unique")
        dimensions.append(
            {
                "name": name,
                "source": source,
                "application": application,
                "values": normalized_values,
            }
        )
    if vector_dimensions > 1:
        raise ValueError("matrix may define at most one vector_id dimension")

    point_count = 1
    for dimension in dimensions:
        point_count *= len(dimension["values"])
        if point_count > _MAX_POINTS:
            raise ValueError(f"matrix has too many points (maximum {_MAX_POINTS})")
    expected = set(
        product(
            *(
                tuple(_value_key(item) for item in dimension["values"])
                for dimension in dimensions
            )
        )
    )
    if len(expected) > _MAX_POINTS:
        raise ValueError(f"matrix has too many points (maximum {_MAX_POINTS})")
    if "expected_point_count" in value:
        declared_count = value["expected_point_count"]
        if isinstance(declared_count, bool) or not isinstance(declared_count, int):
            raise ValueError("matrix expected_point_count must be an integer")
        if declared_count != len(expected):
            raise ValueError("matrix expected_point_count does not match dimensions")
    observed: set[tuple[str, ...]] = set()
    corner_names = {dim["name"] for dim in dimensions if dim["source"] == "corner"}
    for case in cases:
        case_id = case.get("id") if isinstance(case, Mapping) else None
        corner = case.get("corner") if isinstance(case, Mapping) else None
        if corner_names:
            if not isinstance(corner, Mapping) or set(corner) != corner_names:
                raise ValueError(
                    f"matrix case {case_id} corner fields must equal declared dimensions"
                )
        elif corner is not None:
            raise ValueError(f"matrix case {case_id} has undeclared corner metadata")
        if vector_dimensions == 0 and "vector_id" in case:
            raise ValueError(f"matrix case {case_id} has undeclared vector metadata")
        coordinate: list[str] = []
        for dimension in dimensions:
            raw_item = (
                case.get("vector_id")
                if dimension["source"] == "vector_id"
                else corner.get(dimension["name"])
            )
            item = _scalar(
                raw_item,
                f"matrix case {case_id} dimension {dimension['name']}",
            )
            key = _value_key(item)
            allowed = {_value_key(value) for value in dimension["values"]}
            if key not in allowed:
                raise ValueError(
                    f"matrix case {case_id} dimension {dimension['name']} is outside declared values"
                )
            coordinate.append(key)
        point = tuple(coordinate)
        if point in observed:
            raise ValueError(f"matrix case {case_id} duplicates a matrix point")
        observed.add(point)
    if observed != expected:
        raise ValueError(
            "matrix cases must equal the declared exact cross product "
            f"(expected {len(expected)}, observed {len(observed)})"
        )
    return {
        "kind": "exact_cross_product",
        "require_exact_cross_product": True,
        "dimensions": dimensions,
        "expected_point_count": len(expected),
    }


def matrix_evidence(
    matrix: Mapping[str, Any], cases: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Return an auditable summary after the canonical matrix is proven."""
    normalized = normalize_l1_matrix(matrix, cases)
    points = []
    for case in sorted(cases, key=lambda item: str(item.get("id", ""))):
        values: dict[str, Any] = {}
        corner = case.get("corner")
        for dimension in normalized["dimensions"]:
            values[dimension["name"]] = (
                case.get("vector_id")
                if dimension["source"] == "vector_id"
                else corner[dimension["name"]]
            )
        points.append({"case_id": str(case.get("id", "")), "values": values})
    return {
        "kind": normalized["kind"],
        "dimensions": normalized["dimensions"],
        "expected_point_count": normalized["expected_point_count"],
        "observed_point_count": len(cases),
        "coverage": 1.0,
        "points": points,
    }


def validate_observed_matrix(
    matrix: Mapping[str, Any], cases: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Validate observed case metadata against a canonical matrix contract."""
    try:
        normalized = normalize_l1_matrix(matrix, cases)
    except (TypeError, ValueError) as exc:
        return None, [{"code": "matrix_evidence_invalid", "detail": str(exc)}]
    return matrix_evidence(normalized, cases), []


def _scalar(value: object, label: str) -> str | int | float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{label} value must be text or numeric")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label} value must be finite")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{label} text value must be non-empty")
    return value


def _value_key(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Do not coerce integers through ``float``.  Besides raising
        # ``OverflowError`` for sufficiently large JSON integers, that would
        # collapse distinct coordinates above the IEEE-754 exact range.  A
        # Decimal tuple gives us a stable, exact key while still treating
        # numerically equivalent values such as ``1`` and ``1.0`` alike.
        decimal = Decimal(str(value))
        sign, digits, exponent = decimal.as_tuple()
        digits_list = list(digits)
        while digits_list and digits_list[-1] == 0:
            digits_list.pop()
            exponent += 1
        if not digits_list:
            return "number:0"
        return "number:%d:%s:%d" % (
            sign,
            "".join(str(digit) for digit in digits_list),
            exponent,
        )
    return "text:" + json.dumps(value, sort_keys=True, separators=(",", ":"))


__all__ = [
    "matrix_evidence",
    "normalize_l1_matrix",
    "validate_observed_matrix",
]
