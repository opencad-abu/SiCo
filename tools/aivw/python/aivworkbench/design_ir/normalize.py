"""Canonical ordering and redaction-free normalization for DesignIR."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .schema import DesignIR, DesignIRValidationError, validate_design_ir


def normalize_design_ir(value: DesignIR | Mapping[str, Any]) -> dict[str, Any]:
    payload = value.to_dict() if isinstance(value, DesignIR) else validate_design_ir(value)
    result: dict[str, Any] = {
        "schema_version": payload["schema_version"],
        "identity": _sorted_map(payload["identity"]),
    }
    for field in ("hierarchy", "instances", "nets", "terminals"):
        result[field] = _sort_records(payload[field])
    for field in ("ams_binding", "behavior_evidence", "candidate_model"):
        result[field] = _sorted_map(payload[field])
    return result


def _sort_records(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized = [_sorted_map(record) for record in records]
    return sorted(
        normalized,
        key=lambda row: (
            str(row.get("hierarchy_path", row.get("path", ""))),
            str(row.get("name", row.get("id", ""))),
            str(row.get("kind", "")),
        ),
    )


def _sorted_map(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _normalize(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _sorted_map(value)
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize(item) for item in value]
    return value


__all__ = ["normalize_design_ir"]
