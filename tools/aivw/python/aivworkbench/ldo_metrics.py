"""Evaluate deterministic LDO gates from normalized evidence."""

from __future__ import annotations
from typing import Any, Mapping, Sequence

from .metric_errors import LDOMetricError, MetricBoundaryError
from .metric_values import walk_finite
from .metric_results import LDOEvaluation, MetricObservation
from .metric_policy import normalize_assertions, normalize_input, policy_definitions
from .metric_comparison import evaluate_one


def evaluate_metrics(
    policy: object,
    expected: Mapping[str, Any],
    actual: Mapping[str, Any] | None,
    *,
    required_assertions: Sequence[str] = (),
    passed_assertions: Sequence[str] | None = None,
    evidence_status: str = "PASS",
) -> LDOEvaluation:
    """Evaluate one case using categorical, numeric, timing, and waveform rules."""
    try:
        policy = normalize_input(policy, code="BLOCKED_POLICY", label="metric policy")
    except MetricBoundaryError as exc:
        return _blocked(exc.code, str(exc))
    try:
        definitions_input = policy_definitions(policy)
    except Exception as exc:
        return _blocked("BLOCKED_POLICY", "metric policy is malformed: %s" % exc)
    if definitions_input is None:
        return _blocked("BLOCKED_POLICY", "metric policy must be an array or an object with metrics")
    try:
        expected = normalize_input(expected, code="BLOCKED_INPUT", label="expected metrics")
    except MetricBoundaryError as exc:
        return _blocked(exc.code, str(exc))
    if not isinstance(expected, Mapping):
        return _blocked("BLOCKED_INPUT", "expected metrics are not an object")
    try:
        actual = normalize_input(actual, code="BLOCKED_EVIDENCE", label="metric evidence")
    except MetricBoundaryError as exc:
        return _blocked(exc.code, str(exc))
    if not isinstance(actual, Mapping):
        return _blocked("BLOCKED_EVIDENCE", "metric evidence is missing")
    try:
        walk_finite(actual)
    except LDOMetricError as exc:
        return _blocked("BLOCKED_EVIDENCE", str(exc))
    if type(evidence_status) is not str:
        return _blocked("BLOCKED_EVIDENCE", "evidence status must be text")
    if evidence_status != "PASS":
        return _blocked("BLOCKED_EVIDENCE", "evidence status is %s" % evidence_status)
    definitions = {}
    for raw in definitions_input:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("name"), str):
            return _blocked("BLOCKED_POLICY", "metric definition is malformed")
        name = str(raw["name"])
        if name in definitions:
            return _blocked("BLOCKED_POLICY", "duplicate metric %s" % name)
        definitions[name] = raw
    if set(expected) != set(definitions):
        # A case may deliberately omit calibration-only metrics.  They must be
        # marked ``calibration_required`` in policy and are excluded here.
        active = {name for name, raw in definitions.items() if raw.get("calibration_required") is not True}
        if set(expected) != active:
            return _blocked("BLOCKED_INPUT", "expected metric set does not match policy")
        definitions = {name: raw for name, raw in definitions.items() if name in active}
    if set(actual) != set(expected):
        return _blocked("BLOCKED_EVIDENCE", "actual metric set does not match expected")
    observations: list[MetricObservation] = []
    failures = 0
    for name in expected:
        definition = definitions.get(name)
        if definition is None:
            return _blocked("BLOCKED_POLICY", "unknown expected metric %s" % name)
        kind = definition.get("kind")
        wanted = expected[name]
        got = actual[name]
        try:
            observation = evaluate_one(name, str(kind), wanted, got, definition)
        except MetricBoundaryError as exc:
            return _blocked(exc.code, str(exc))
        except LDOMetricError as exc:
            return _blocked("BLOCKED_EVIDENCE", str(exc))
        observations.append(observation)
        failures += int(not observation.passed)
    try:
        required_values = normalize_assertions(
            required_assertions, code="BLOCKED_INPUT", label="required_assertions"
        )
        observed_values = normalize_assertions(
            passed_assertions, code="BLOCKED_EVIDENCE", label="passed_assertions"
        )
    except MetricBoundaryError as exc:
        return _blocked(exc.code, str(exc))
    required = set(required_values)
    observed_assertions = set(observed_values)
    missing_assertions = sorted(required - observed_assertions)
    if missing_assertions:
        return LDOEvaluation(
            "FAIL_ASSERTION", "assertion_mismatch", tuple(observations),
            {"metric_failure_count": failures, "missing_assertions": missing_assertions},
        )
    status = "PASS" if failures == 0 else "FAIL_CORRELATION"
    return LDOEvaluation(
        status,
        "all_metrics_match" if status == "PASS" else "metric_mismatch",
        tuple(observations),
        {"metric_failure_count": failures, "missing_assertions": []},
    )


def evaluate_case(case: Mapping[str, Any], policy: object, evidence: Mapping[str, Any] | None) -> LDOEvaluation:
    try:
        case = normalize_input(case, code="BLOCKED_INPUT", label="case")
    except MetricBoundaryError as exc:
        return _blocked(exc.code, str(exc))
    if not isinstance(case, Mapping):
        return _blocked("BLOCKED_INPUT", "case is not an object")
    if evidence is not None:
        try:
            evidence = normalize_input(evidence, code="BLOCKED_EVIDENCE", label="evidence")
        except MetricBoundaryError as exc:
            return _blocked(exc.code, str(exc))
    if evidence is not None and not isinstance(evidence, Mapping):
        return _blocked("BLOCKED_EVIDENCE", "evidence is not an object")
    evidence_metrics = evidence.get("metrics") if isinstance(evidence, Mapping) else None
    passed = evidence.get("passed_assertions", ()) if isinstance(evidence, Mapping) else None
    status = evidence.get("status", "MISSING") if isinstance(evidence, Mapping) else "MISSING"
    return evaluate_metrics(
        policy,
        case.get("expected", {}),
        evidence_metrics,
        required_assertions=case.get("assertions", ()),
        passed_assertions=passed,
        evidence_status=status,
    )


def _blocked(code: str, detail: str) -> LDOEvaluation:
    return LDOEvaluation(code, code, (), {"detail": detail})



__all__ = ["LDOMetricError", "MetricObservation", "LDOEvaluation", "evaluate_metrics", "evaluate_case"]
