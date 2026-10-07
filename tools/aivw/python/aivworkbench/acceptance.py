"""Unit-aware acceptance and reproducibility primitives, independent of targets.

Product goals and reference-model agreement are separate evaluations. Missing
units, tolerances, evidence, or a scale at zero never produce a passing result.
"""

from __future__ import annotations

from decimal import Decimal
import json
import math
from typing import Any, Mapping, Sequence

from .workspace import stable_digest


def finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be a finite number" % label)
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("%s must be a finite number" % label)
    return result


def validate_tolerance(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "unit", "relative", "absolute", "normalization"
    }:
        raise ValueError("tolerance fields must be unit/relative/absolute/normalization")
    if not isinstance(value["unit"], str) or not value["unit"]:
        raise ValueError("tolerance requires a unit")
    if value["normalization"] != "reference_magnitude":
        raise ValueError("unsupported tolerance normalization")
    normalized = dict(value)
    for name in ("relative", "absolute"):
        if value[name] is not None:
            normalized[name] = finite(value[name], name)
            if normalized[name] < 0:
                raise ValueError("tolerance cannot be negative")
    if normalized["relative"] is None and normalized["absolute"] is None:
        raise ValueError("at least one tolerance must be defined")
    return normalized


def compare_numeric(actual: object, reference: object, *, unit: str,
                    tolerance: Mapping[str, Any]) -> dict[str, Any]:
    """Compare against max(absolute, relative * abs(reference)), inclusively.

Decimal arithmetic preserves inclusive decimal boundaries such as 2.575 V at
3% of 2.5 V. A zero reference needs an explicitly supplied absolute tolerance.
No solver tolerance or target-specific constant is silently substituted.
"""
    rule = validate_tolerance(tolerance)
    if unit != rule["unit"]:
        raise ValueError("measurement and tolerance units differ")
    got, want = finite(actual, "actual"), finite(reference, "reference")
    error = abs(Decimal(str(got)) - Decimal(str(want)))
    base = {"actual": got, "reference": want, "unit": unit,
            "absolute_error": float(error), "tolerance": rule}
    if want == 0.0 and rule["absolute"] is None:
        return {**base, "status": "BLOCKED_CONTRACT", "reason": "zero_reference_requires_absolute_tolerance"}
    absolute = Decimal(str(rule["absolute"] or 0.0))
    relative = Decimal(str(rule["relative"] or 0.0)) * abs(Decimal(str(want)))
    limit = max(absolute, relative)
    if not math.isfinite(float(error)) or not math.isfinite(float(limit)):
        raise ValueError("numeric comparison overflow")
    return {**base, "status": "PASS" if error <= limit else "FAIL_TOLERANCE",
            "limit": float(limit),
            "relative_error": float(error / abs(Decimal(str(want)))) if want else None}


def compare_waveform(actual: Sequence[object], reference: Sequence[object], *,
                     times: Sequence[object], unit: str,
                     tolerance: Mapping[str, Any]) -> dict[str, Any]:
    """Require already aligned, finite samples; every sample must pass."""
    if not len(times) == len(actual) == len(reference) or len(times) < 2:
        raise ValueError("waveform samples must share a nonempty aligned time axis")
    axis = [finite(t, "time") for t in times]
    if any(b <= a for a, b in zip(axis, axis[1:])):
        raise ValueError("waveform time must increase strictly")
    results = [compare_numeric(a, r, unit=unit, tolerance=tolerance)
               for a, r in zip(actual, reference)]
    statuses = {r["status"] for r in results}
    status = ("BLOCKED_CONTRACT" if "BLOCKED_CONTRACT" in statuses else
              "FAIL_TOLERANCE" if "FAIL_TOLERANCE" in statuses else "PASS")
    return {"status": status, "sample_count": len(results),
            "max_absolute_error": max(r["absolute_error"] for r in results),
            "failed_samples": sum(r["status"] == "FAIL_TOLERANCE" for r in results),
            "blocked_samples": sum(r["status"] == "BLOCKED_CONTRACT" for r in results)}


def compare_repeats(runs: Sequence[Mapping[str, Any]], *, required_runs: int = 2) -> dict[str, Any]:
    """Compare frozen execution inputs and normalized measurements, not logs.

The caller must authenticate manifests/artifacts before constructing records.
Exact measurement equality is the initial deterministic replay policy; it is
separate from a much looser model-versus-reference accuracy tolerance.
"""
    if isinstance(required_runs, bool) or not isinstance(required_runs, int) or required_runs < 2:
        raise ValueError("repeat requires at least two runs")
    if len(runs) != required_runs:
        return {"status": "BLOCKED_EVIDENCE", "reason": "repeat_count", "run_count": len(runs)}
    required = {"run_id", "manifest_status", "execution_status", "inputs", "measurements"}
    for run in runs:
        if set(run) != required or run["manifest_status"] != "PASS" or run["execution_status"] != "PASS":
            raise ValueError("repeat needs authenticated successful execution evidence")
        if not isinstance(run["run_id"], str) or not run["run_id"]:
            raise ValueError("repeat run identity is missing")
        if not isinstance(run["inputs"], Mapping) or not run["inputs"] or not run["measurements"]:
            raise ValueError("repeat input/measurement identity is missing")
        try:
            json.dumps({"inputs":run["inputs"], "measurements":run["measurements"]}, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("repeat record contains non-finite or non-JSON data") from exc
    if len({run["run_id"] for run in runs}) != len(runs):
        raise ValueError("repeat must use independent run IDs")
    inputs = [stable_digest(run["inputs"]) for run in runs]
    measurements = [stable_digest(run["measurements"]) for run in runs]
    return {"status": "PASS" if len(set(inputs)) == len(set(measurements)) == 1 else "FAIL_REPEAT",
            "run_ids": [run["run_id"] for run in runs], "run_count": len(runs),
            "input_digests": inputs, "measurement_digests": measurements,
            "comparison": "identical_normalized_measurements"}
