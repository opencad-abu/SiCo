from __future__ import annotations

import math
from typing import Any, Mapping

from .errors import EnvironmentError
from .m1ai_correlation_sources import _validate_source_run

_EXPECTED_KINDS = {"spectre": "spectre-correlation-evidence", "rnm": "rnm-correlation-evidence"}

def _validate_evidence(
    payload: Mapping[str, Any], *, role: str, policy: Mapping[str, Any]
) -> dict[str, Any]:
    expected_kind = _EXPECTED_KINDS[role]
    if payload.get("schema_version") != 1 or payload.get("kind") != expected_kind:
        raise EnvironmentError(f"{role} evidence has an unsupported schema or kind")
    target = payload.get("target")
    policy_target = policy["target"]
    if not isinstance(target, Mapping) or any(
        str(target.get(key, "")) != str(policy_target[key])
        for key in ("library", "cell", "module")
    ):
        raise EnvironmentError(f"{role} evidence target does not match policy")
    source_run = _validate_source_run(payload.get("source_run"), role=role)
    stimulus = payload.get("stimulus")
    if not isinstance(stimulus, Mapping):
        raise EnvironmentError(f"{role} evidence requires normalized stimulus provenance")
    stimulus_corner = stimulus.get("corner")
    clock = stimulus.get("clock")
    stimulus_cases = stimulus.get("cases")
    if (
        not isinstance(stimulus_corner, Mapping)
        or not isinstance(clock, Mapping)
        or not isinstance(stimulus_cases, Mapping)
    ):
        raise EnvironmentError(f"{role} evidence stimulus requires corner, clock, and cases")
    edge = str(clock.get("edge", ""))
    if edge != "rising":
        raise EnvironmentError(f"{role} evidence uses an unsupported clock edge: {edge}")
    sample_delay = _numeric(
        clock.get("sample_delay_seconds"), label=f"{role} stimulus sample delay"
    )
    if sample_delay < 0:
        raise EnvironmentError(f"{role} stimulus sample delay must not be negative")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EnvironmentError(f"{role} evidence must contain non-empty cases")
    required_metrics = set(policy["metrics"])
    required_types = set(str(value) for value in policy["required_case_types"])
    normalized: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, Mapping):
            raise EnvironmentError(f"{role} evidence contains a non-object case")
        case_id = str(case.get("id", ""))
        case_type = str(case.get("type", ""))
        corner = case.get("corner")
        measurements = case.get("measurements")
        if not case_id or case_id in normalized:
            raise EnvironmentError(f"{role} evidence has duplicate or empty case id")
        if case_type not in required_types:
            raise EnvironmentError(f"{role} evidence has unsupported case type: {case_type}")
        if not isinstance(corner, Mapping) or not isinstance(measurements, Mapping):
            raise EnvironmentError(f"{role} case {case_id} lacks corner or measurements")
        if dict(corner) != dict(stimulus_corner):
            raise EnvironmentError(f"{role} case {case_id} corner differs from stimulus corner")
        vector = stimulus_cases.get(case_id)
        if not isinstance(vector, Mapping) or str(vector.get("type", "")) != case_type:
            raise EnvironmentError(f"{role} case {case_id} lacks matching stimulus vector")
        din_p = _numeric(vector.get("Din+"), label=f"{role} case {case_id} Din+")
        din_n = _numeric(vector.get("Din-"), label=f"{role} case {case_id} Din-")
        missing = sorted(required_metrics - set(measurements))
        if missing:
            raise EnvironmentError(f"{role} case {case_id} misses metrics: {missing}")
        normalized[case_id] = {
            "id": case_id,
            "type": case_type,
            "corner": dict(corner),
            "measurements": dict(measurements),
            "stimulus": {"Din+": din_p, "Din-": din_n},
        }
    present_types = {case["type"] for case in normalized.values()}
    missing_types = sorted(required_types - present_types)
    if missing_types:
        raise EnvironmentError(f"{role} evidence misses case types: {missing_types}")
    extra_stimulus = sorted(set(str(key) for key in stimulus_cases) - set(normalized))
    if extra_stimulus:
        raise EnvironmentError(f"{role} evidence has extra stimulus cases: {extra_stimulus}")
    return {
        "source_run": source_run,
        "cases": normalized,
        "stimulus": {
            "corner": dict(stimulus_corner),
            "clock": {"edge": edge, "sample_delay_seconds": sample_delay},
            "cases": {
                case_id: {
                    "type": case["type"],
                    "Din+": case["stimulus"]["Din+"],
                    "Din-": case["stimulus"]["Din-"],
                }
                for case_id, case in normalized.items()
            },
        },
    }
def _numeric(value: object, *, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise EnvironmentError(f"{label} must be numeric")
    return float(value)

__all__=["_validate_evidence","_numeric"]
