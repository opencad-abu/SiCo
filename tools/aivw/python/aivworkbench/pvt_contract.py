"""Fail-closed physical PVT/corner/vector contract normalization.

This module defines the input boundary for a future Spectre/AMS physical-corner
adapter.  It deliberately does not launch a simulator, select a process model,
or inspect PSF data.  The generic workflow can use the normalized result to
decide whether a registered adapter is allowed to start; missing tools, model
files, or an already-used output directory remain explicit blocked states.

The existing comparator matrix is intentionally separate from this contract:
its ``process`` and ``temperature`` coordinates are metadata-only.  A matrix
becomes a physical PVT request only when it declares a model-section binding
and an adapter-owned launcher contract here.
"""

from __future__ import annotations

from decimal import Decimal
import itertools
import json
import math
from pathlib import PurePosixPath
import re
from typing import Any, Mapping, Sequence

PVT_CONTRACT_VERSION = 1
PVT_READY = "READY"
PVT_BLOCKED_INPUT = "BLOCKED_INPUT"
PVT_BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"
PVT_STALE_ARTIFACT = "STALE_ARTIFACT"

_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_SAFE_SECTION = re.compile(r"^[A-Za-z0-9_.+~-]+$")
_SOURCES = {"corner", "vector_id"}
_ROLES = {"process", "temperature", "supply", "vector", "context"}
_SIMULATORS = {"spectre", "ams"}
_RAW_OUTPUTS = {"psf", "shm", "fsdb"}
_MAX_POINTS = 4096


class PVTContractError(ValueError):
    """Raised when a physical PVT contract is malformed."""


def normalize_physical_pvt_matrix(
    value: object,
    cases: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize a physical PVT exact product and optional observed cases.

    The contract requires one explicitly labelled process, temperature, and
    supply axis.  A vector axis is optional and must come from ``vector_id``.
    Process values are mapped one-to-one to approved model sections; no section
    name is inferred from a process spelling.
    """

    if cases is not None and (
        not isinstance(cases, Sequence) or isinstance(cases, (str, bytes))
    ):
        raise PVTContractError("physical PVT cases must be an array")
    if not isinstance(value, Mapping):
        raise PVTContractError("physical PVT contract must be an object")
    allowed = {
        "schema_version",
        "kind",
        "require_exact_cross_product",
        "dimensions",
        "expected_point_count",
        "model_binding",
        "execution",
    }
    required = {
        "schema_version",
        "kind",
        "require_exact_cross_product",
        "dimensions",
        "model_binding",
        "execution",
    }
    if set(value) - allowed or not required.issubset(value):
        raise PVTContractError("physical PVT contract fields are invalid")
    if value.get("schema_version") != PVT_CONTRACT_VERSION:
        raise PVTContractError("unsupported physical PVT contract schema_version")
    if value.get("kind") != "physical_pvt_exact_cross_product":
        raise PVTContractError("physical PVT kind is unsupported")
    if value.get("require_exact_cross_product") is not True:
        raise PVTContractError("physical PVT matrix must require an exact cross product")

    dimensions = _normalize_dimensions(value.get("dimensions"))
    by_role = {item["role"]: item for item in dimensions}
    for role in ("process", "temperature", "supply"):
        if role not in by_role:
            raise PVTContractError(f"physical PVT matrix requires a {role} dimension")
    if sum(item["role"] == "vector" for item in dimensions) > 1:
        raise PVTContractError("physical PVT matrix may define at most one vector dimension")

    point_count = 1
    for dimension in dimensions:
        point_count *= len(dimension["values"])
        if point_count > _MAX_POINTS:
            raise PVTContractError(
                f"physical PVT matrix has too many points (maximum {_MAX_POINTS})"
            )

    expected = value.get("expected_point_count", point_count)
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise PVTContractError("physical PVT expected_point_count must be an integer")
    if expected < 1 or expected > _MAX_POINTS or expected != point_count:
        raise PVTContractError(
            "physical PVT expected_point_count does not match dimensions"
        )

    model_binding = _normalize_model_binding(value.get("model_binding"), by_role["process"])
    execution = _normalize_execution(value.get("execution"))

    normalized: dict[str, Any] = {
        "schema_version": PVT_CONTRACT_VERSION,
        "kind": "physical_pvt_exact_cross_product",
        "require_exact_cross_product": True,
        "dimensions": dimensions,
        "expected_point_count": point_count,
        "model_binding": model_binding,
        "execution": execution,
    }
    normalized["points"] = _build_points(normalized, cases)
    if cases is not None:
        _validate_cases(normalized, cases)
    return normalized


def _normalize_dimensions(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise PVTContractError("physical PVT dimensions must be a non-empty array")
    dimensions: list[dict[str, Any]] = []
    names: set[str] = set()
    roles: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise PVTContractError("physical PVT dimension must be an object")
        if set(raw) != {"name", "source", "role", "values"}:
            raise PVTContractError("physical PVT dimension fields are invalid")
        name = raw.get("name")
        source = raw.get("source")
        role = raw.get("role")
        values = raw.get("values")
        if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
            raise PVTContractError("physical PVT dimension name is invalid")
        if name in names:
            raise PVTContractError("physical PVT dimension names must be unique")
        names.add(name)
        if source not in _SOURCES:
            raise PVTContractError("physical PVT dimension source is invalid")
        if role not in _ROLES:
            raise PVTContractError("physical PVT dimension role is invalid")
        if role in roles and role in {"process", "temperature", "supply", "vector"}:
            raise PVTContractError(f"physical PVT role must be unique: {role}")
        roles.add(role)
        if role == "vector" and source != "vector_id":
            raise PVTContractError("physical PVT vector role must use vector_id")
        if role != "vector" and source == "vector_id":
            raise PVTContractError("only the vector role may use vector_id")
        if not isinstance(values, list) or not values:
            raise PVTContractError(f"physical PVT dimension {name} values must be non-empty")
        normalized_values = [
            _dimension_value(item, role, f"physical PVT dimension {name}")
            for item in values
        ]
        keys = [_value_key(item) for item in normalized_values]
        if len(keys) != len(set(keys)):
            raise PVTContractError(f"physical PVT dimension {name} values must be unique")
        dimensions.append(
            {
                "name": name,
                "source": source,
                "role": role,
                "values": normalized_values,
            }
        )
    return dimensions


def _dimension_value(value: object, role: str, label: str) -> str | int | float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise PVTContractError(f"{label} values must be scalar")
    if isinstance(value, str):
        if not value.strip():
            raise PVTContractError(f"{label} text values must be non-empty")
        if role in {"temperature", "supply"}:
            raise PVTContractError(f"{label} {role} values must be numeric")
        return value
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PVTContractError(f"{label} numeric values must be finite") from exc
    if not math.isfinite(numeric):
        raise PVTContractError(f"{label} numeric values must be finite")
    if role in {"process", "vector"}:
        raise PVTContractError(f"{label} {role} values must be text")
    return value


def _normalize_model_binding(
    value: object, process_dimension: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PVTContractError("physical PVT model_binding must be an object")
    if set(value) != {"model_file", "section_dimension", "section_by_value"}:
        raise PVTContractError("physical PVT model_binding fields are invalid")
    model_file = value.get("model_file")
    if not _safe_relative_path(model_file):
        raise PVTContractError("physical PVT model_file must be a safe relative path")
    if value.get("section_dimension") != process_dimension["name"]:
        raise PVTContractError(
            "physical PVT section_dimension must identify the process dimension"
        )
    raw_sections = value.get("section_by_value")
    if not isinstance(raw_sections, Mapping):
        raise PVTContractError("physical PVT section_by_value must be an object")
    expected_keys = {_value_key(item) for item in process_dimension["values"]}
    actual_keys = {_value_key(key) for key in raw_sections}
    if expected_keys != actual_keys:
        raise PVTContractError(
            "physical PVT section_by_value keys must equal process values"
        )
    sections: dict[str, str] = {}
    for process_value in process_dimension["values"]:
        lookup = _lookup_mapping_value(raw_sections, process_value)
        if not isinstance(lookup, str) or not _SAFE_SECTION.fullmatch(lookup):
            raise PVTContractError("physical PVT model section names are invalid")
        # Process values are text by contract, so retain their declared spelling
        # in the public mapping.  The normalized point table may still use the
        # stable value key internally when looking up a section.
        sections[str(process_value)] = lookup
    if len(set(sections.values())) != len(sections):
        raise PVTContractError("physical PVT model sections must be unique")
    return {
        "model_file": str(model_file),
        "section_dimension": process_dimension["name"],
        "section_by_value": sections,
    }


def _normalize_execution(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise PVTContractError("physical PVT execution must be an object")
    if set(value) != {"simulator", "launcher", "raw_output", "payload_relative_root"}:
        raise PVTContractError("physical PVT execution fields are invalid")
    simulator = value.get("simulator")
    launcher = value.get("launcher")
    raw_output = value.get("raw_output")
    output_root = value.get("payload_relative_root")
    if simulator not in _SIMULATORS:
        raise PVTContractError("physical PVT simulator must be spectre or ams")
    if not isinstance(launcher, str) or not _SAFE_NAME.fullmatch(launcher):
        raise PVTContractError("physical PVT launcher must be a registered safe ID")
    if raw_output not in _RAW_OUTPUTS:
        raise PVTContractError("physical PVT raw_output is unsupported")
    if not _safe_relative_path(output_root):
        raise PVTContractError(
            "physical PVT payload_relative_root must be a safe relative path"
        )
    return {
        "simulator": str(simulator),
        "launcher": launcher,
        "raw_output": str(raw_output),
        "payload_relative_root": str(output_root),
    }


def _build_points(
    contract: Mapping[str, Any], cases: Sequence[Mapping[str, Any]] | None
) -> list[dict[str, Any]]:
    dimensions = contract["dimensions"]
    case_by_coordinate: dict[tuple[str, ...], str] = {}
    if cases is not None:
        seen_ids: set[str] = set()
        for case in cases:
            if not isinstance(case, Mapping):
                raise PVTContractError("physical PVT cases must contain objects")
            case_id = case.get("id")
            if (
                not isinstance(case_id, str)
                or not _SAFE_NAME.fullmatch(case_id)
                or case_id in seen_ids
            ):
                raise PVTContractError("physical PVT cases must define unique safe IDs")
            seen_ids.add(case_id)
            coordinate = _case_coordinate(contract, case)
            if coordinate in case_by_coordinate:
                raise PVTContractError("physical PVT cases duplicate a matrix point")
            case_by_coordinate[coordinate] = case_id

    value_product = itertools.product(*(dimension["values"] for dimension in dimensions))
    points: list[dict[str, Any]] = []
    for index, values in enumerate(value_product):
        coordinate = tuple(_value_key(item) for item in values)
        process_value = values[next(i for i, item in enumerate(dimensions) if item["role"] == "process")]
        point: dict[str, Any] = {
            "id": case_by_coordinate.get(coordinate, f"pvt-{index:04d}"),
            "coordinates": {
                dimension["name"]: item
                for dimension, item in zip(dimensions, values)
            },
            "model_file": contract["model_binding"]["model_file"],
            "model_section": _lookup_mapping_value(
                contract["model_binding"]["section_by_value"], process_value
            ),
        }
        vector = next((item for item in dimensions if item["role"] == "vector"), None)
        if vector is not None:
            point["vector_id"] = point["coordinates"][vector["name"]]
        points.append(point)
    return points


def _validate_cases(contract: Mapping[str, Any], cases: Sequence[Mapping[str, Any]]) -> None:
    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)):
        raise PVTContractError("physical PVT cases must be an array")
    if len(cases) != contract["expected_point_count"]:
        raise PVTContractError(
            "physical PVT cases must equal the declared exact cross product"
        )
    expected_ids = {point["id"] for point in contract["points"]}
    actual_ids = {case.get("id") for case in cases if isinstance(case, Mapping)}
    if expected_ids != actual_ids:
        raise PVTContractError("physical PVT case IDs do not match normalized points")


def _case_coordinate(
    contract: Mapping[str, Any], case: Mapping[str, Any]
) -> tuple[str, ...]:
    dimensions = contract["dimensions"]
    corner_dimensions = [item for item in dimensions if item["source"] == "corner"]
    corner = case.get("corner")
    if corner_dimensions:
        if not isinstance(corner, Mapping) or set(corner) != {
            item["name"] for item in corner_dimensions
        }:
            raise PVTContractError("physical PVT case corner fields are incomplete or extra")
    elif corner is not None:
        raise PVTContractError("physical PVT case has undeclared corner metadata")
    vector_dimensions = [item for item in dimensions if item["source"] == "vector_id"]
    if not vector_dimensions and "vector_id" in case:
        raise PVTContractError("physical PVT case has undeclared vector metadata")
    coordinate: list[str] = []
    for dimension in dimensions:
        raw = (
            case.get("vector_id")
            if dimension["source"] == "vector_id"
            else corner.get(dimension["name"])
        )
        value = _dimension_value(raw, dimension["role"], f"physical PVT case {case.get('id')}")
        key = _value_key(value)
        allowed = {_value_key(item) for item in dimension["values"]}
        if key not in allowed:
            raise PVTContractError(
                f"physical PVT case {case.get('id')} value is outside declared values"
            )
        coordinate.append(key)
    return tuple(coordinate)


def _lookup_mapping_value(mapping: Mapping[Any, Any], value: object) -> object:
    target = _value_key(value)
    for key, candidate in mapping.items():
        if _value_key(key) == target:
            return candidate
    return None


def _value_key(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
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


def _safe_relative_path(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or re.match(r"^[A-Za-z]:", value)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        return False
    path = PurePosixPath(value)
    return (
        path.as_posix() == value
        and not path.is_absolute()
        and all(part not in {".", "..", ""} for part in value.split("/"))
    )


def preflight_physical_pvt(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Compatibility wrapper for the read-only preflight implementation."""

    from .pvt_preflight import preflight_physical_pvt as implementation

    return implementation(*args, **kwargs)


__all__ = [
    "PVT_BLOCKED_ENVIRONMENT",
    "PVT_BLOCKED_INPUT",
    "PVT_CONTRACT_VERSION",
    "PVT_READY",
    "PVT_STALE_ARTIFACT",
    "PVTContractError",
    "normalize_physical_pvt_matrix",
    "preflight_physical_pvt",
]
