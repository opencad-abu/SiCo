"""Compare one metric using its declared deterministic rule."""

from __future__ import annotations
import math
from typing import Any, Mapping, Sequence

from .metric_errors import LDOMetricError, MetricBoundaryError
from .metric_values import finite
from .metric_results import MetricObservation


def evaluate_one(name: str, kind: str, expected: object, actual: object, definition: Mapping[str, Any]) -> MetricObservation:
    if kind == "categorical":
        passed = type(actual) is type(expected) and actual == expected
        return MetricObservation(name, kind, expected, actual, passed, {"comparison": "exact"})
    if kind == "allowed_set":
        allowed = definition.get("allowed", expected if isinstance(expected, (list, tuple, set)) else [])
        if not isinstance(allowed, (list, tuple, set)):
            raise MetricBoundaryError("BLOCKED_POLICY", "%s allowed set is invalid" % name)
        passed = actual in allowed
        return MetricObservation(name, kind, expected, actual, passed, {"allowed": list(allowed)})
    if kind in {"numeric", "timing"}:
        try:
            want = finite(expected, "%s.expected" % name)
        except LDOMetricError as exc:
            raise MetricBoundaryError("BLOCKED_INPUT", str(exc)) from exc
        try:
            got = finite(actual, "%s.actual" % name)
        except LDOMetricError as exc:
            raise MetricBoundaryError("BLOCKED_EVIDENCE", str(exc)) from exc
        tolerance = definition.get("absolute_tolerance", definition.get("tolerance", 0.0))
        try:
            tolerance = finite(tolerance, "%s.tolerance" % name)
        except LDOMetricError as exc:
            raise MetricBoundaryError("BLOCKED_POLICY", str(exc)) from exc
        if tolerance < 0:
            raise MetricBoundaryError("BLOCKED_POLICY", "%s.tolerance must be non-negative" % name)
        delta = abs(got - want)
        if not math.isfinite(delta):
            raise MetricBoundaryError("BLOCKED_INPUT", "%s numeric comparison overflowed" % name)
        return MetricObservation(name, kind, expected, actual, delta <= tolerance, {"absolute_error": delta, "absolute_tolerance": tolerance})
    if kind == "waveform":
        if not isinstance(expected, Sequence) or isinstance(expected, (str, bytes)):
            raise MetricBoundaryError("BLOCKED_INPUT", "%s expected waveform must be an array" % name)
        if not isinstance(actual, Sequence) or isinstance(actual, (str, bytes)):
            raise MetricBoundaryError("BLOCKED_EVIDENCE", "%s actual waveform must be an array" % name)
        if len(expected) != len(actual) or not expected:
            return MetricObservation(name, kind, expected, actual, False, {"comparison": "samplewise", "length_match": len(expected) == len(actual)})
        try:
            tolerance = finite(definition.get("absolute_tolerance", 0.0), "%s.tolerance" % name)
        except LDOMetricError as exc:
            raise MetricBoundaryError("BLOCKED_POLICY", str(exc)) from exc
        if tolerance < 0:
            raise MetricBoundaryError("BLOCKED_POLICY", "%s.tolerance must be non-negative" % name)
        errors = []
        for index, (want, got) in enumerate(zip(expected, actual)):
            try:
                got_value = finite(got, "%s.actual[%d]" % (name, index))
            except LDOMetricError as exc:
                raise MetricBoundaryError("BLOCKED_EVIDENCE", str(exc)) from exc
            try:
                want_value = finite(want, "%s.expected[%d]" % (name, index))
            except LDOMetricError as exc:
                raise MetricBoundaryError("BLOCKED_INPUT", str(exc)) from exc
            error = abs(got_value - want_value)
            if not math.isfinite(error):
                raise MetricBoundaryError("BLOCKED_INPUT", "%s waveform comparison overflowed at index %d" % (name, index))
            errors.append(error)
        maximum = max(errors)
        return MetricObservation(name, kind, expected, actual, maximum <= tolerance, {"max_absolute_error": maximum, "absolute_tolerance": tolerance, "sample_count": len(errors)})
    if kind == "statistical":
        # Statistical metrics are not part of the M1 required deterministic
        # slice; requiring an explicit calibration marker avoids false PASS.
        raise MetricBoundaryError("BLOCKED_POLICY", "%s statistical metric requires calibration" % name)
    raise MetricBoundaryError("BLOCKED_POLICY", "unsupported metric kind %s" % kind)

