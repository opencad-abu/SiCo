"""Strict DesignIR v2 schema and bounded schematic-to-IR adapter."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

DESIGN_IR_VERSION = 2
_TOP_KEYS = frozenset(
    {"schema_version", "identity", "hierarchy", "instances", "nets", "terminals", "ams_binding", "behavior_evidence", "candidate_model"}
)
_IDENTITY_KEYS = frozenset({"library", "cell", "module", "source_generation", "view"})


class DesignIRValidationError(ValueError):
    """Raised when a DesignIR cannot be made canonical and target-bound."""


def _text(value: Any, field: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise DesignIRValidationError(f"{field} must be a non-empty bounded string")
    return value


def _object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DesignIRValidationError(f"{field} must be an object")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise DesignIRValidationError(f"{field} must be an array")
    if len(value) > 100_000:
        raise DesignIRValidationError(f"{field} exceeds the bounded item limit")
    return value


def _record_list(value: Any, field: str) -> list[dict[str, Any]]:
    rows = _list(value, field)
    output: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        record = dict(_object(row, f"{field}[{index}]"))
        name = record.get("name") or record.get("path") or record.get("id")
        _text(name, f"{field}[{index}].name")
        output.append(record)
    return output


@dataclass(frozen=True)
class DesignIR:
    """Validated, immutable-at-boundary DesignIR payload."""

    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", validate_design_ir(self.payload))

    @property
    def source_generation(self) -> str:
        return str(self.payload["identity"]["source_generation"])

    def to_dict(self) -> dict[str, Any]:
        return _copy_json(self.payload)


def validate_design_ir(value: Mapping[str, Any]) -> dict[str, Any]:
    root = dict(_object(value, "design_ir"))
    unknown = sorted(set(root) - _TOP_KEYS)
    if unknown:
        raise DesignIRValidationError(f"design_ir has unknown fields: {unknown}")
    if root.get("schema_version") != DESIGN_IR_VERSION:
        raise DesignIRValidationError("design_ir schema_version must be 2")
    identity = dict(_object(root.get("identity"), "identity"))
    unknown_identity = sorted(set(identity) - _IDENTITY_KEYS)
    if unknown_identity:
        raise DesignIRValidationError(f"identity has unknown fields: {unknown_identity}")
    for field in ("library", "cell", "module", "source_generation", "view"):
        _text(identity.get(field), f"identity.{field}")
    for field in ("hierarchy", "instances", "nets", "terminals"):
        root[field] = _record_list(root.get(field), field)
    for field in ("ams_binding", "behavior_evidence", "candidate_model"):
        candidate = root.get(field, {})
        if not isinstance(candidate, Mapping):
            raise DesignIRValidationError(f"{field} must be an object")
        root[field] = dict(candidate)
    root["identity"] = identity
    return _copy_json(root)


def build_design_ir(
    *,
    identity: Mapping[str, Any],
    hierarchy: list[Mapping[str, Any]] | None = None,
    instances: list[Mapping[str, Any]] | None = None,
    nets: list[Mapping[str, Any]] | None = None,
    terminals: list[Mapping[str, Any]] | None = None,
    ams_binding: Mapping[str, Any] | None = None,
    behavior_evidence: Mapping[str, Any] | None = None,
    candidate_model: Mapping[str, Any] | None = None,
) -> DesignIR:
    return DesignIR(
        {
            "schema_version": DESIGN_IR_VERSION,
            "identity": dict(identity),
            "hierarchy": [dict(item) for item in (hierarchy or [])],
            "instances": [dict(item) for item in (instances or [])],
            "nets": [dict(item) for item in (nets or [])],
            "terminals": [dict(item) for item in (terminals or [])],
            "ams_binding": dict(ams_binding or {}),
            "behavior_evidence": dict(behavior_evidence or {}),
            "candidate_model": dict(candidate_model or {}),
        }
    )


def _copy_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _copy_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    if isinstance(value, (str, bool, int)) or value is None:
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise DesignIRValidationError("design_ir contains a non-finite or non-JSON value")


__all__ = ["DESIGN_IR_VERSION", "DesignIR", "DesignIRValidationError", "build_design_ir", "validate_design_ir"]
