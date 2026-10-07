"""Canonical experiment values and scalar input contracts."""

from __future__ import annotations


import hashlib

import json

import math

import re

from types import MappingProxyType

from typing import Any, Mapping

from .ldo_experiment_errors import LDOExperimentPolicyError

TOPOLOGIES = {"LDO_MASTER", "LDO_AON"}


SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


def freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


def thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


def canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def mapping(value: object, label: str, fields: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LDOExperimentPolicyError("%s must be an object" % label)
    unknown = set(value) - fields
    missing = fields - set(value)
    if unknown or missing:
        raise LDOExperimentPolicyError(
            "%s fields mismatch: missing=%s unknown=%s"
            % (label, sorted(missing), sorted(str(item) for item in unknown))
        )
    return value


def finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LDOExperimentPolicyError("%s must be numeric" % label)
    result = float(value)
    if not math.isfinite(result):
        raise LDOExperimentPolicyError("%s must be finite" % label)
    return result


def positive(value: object, label: str) -> float:
    result = finite(value, label)
    if result <= 0.0:
        raise LDOExperimentPolicyError("%s must be positive" % label)
    return result


def bounded_count(value: object, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LDOExperimentPolicyError("%s must be a positive integer" % label)
    if value > maximum:
        raise LDOExperimentPolicyError(
            "%s exceeds the hard limit of %d" % (label, maximum)
        )
    return value


def identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise LDOExperimentPolicyError("%s must be an event-safe identifier" % label)
    return value


def normalize_topology(value: object) -> str:
    if not isinstance(value, str) or value not in TOPOLOGIES:
        raise LDOExperimentPolicyError("unsupported LDO topology/cell: %r" % value)
    return value
