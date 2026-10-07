"""Validate a complete public plan against deterministic policy."""

from __future__ import annotations


import math


from typing import Any, Mapping

from .ldo_experiment_errors import LDOExperimentSafetyError
from .ldo_experiment_values import normalize_topology
from .ldo_experiment_policy import LDOExperimentPolicy, canonicalize_experiment_policy
from .ldo_experiment_case import case_from_mapping
from .ldo_experiment_plan import LDOExperimentPlan, PLAN_BUDGET_FIELDS, PLAN_FIELDS
from .ldo_experiment_cases import expected_cases
from .ldo_experiment_budget import budget_projection, check_budget
from .ldo_experiment_safety import validate_case_safety
from .ldo_experiment_holdout import assert_no_holdout_leak

def validate_experiment_plan(
    plan: LDOExperimentPlan | Mapping[str, Any],
    policy: LDOExperimentPolicy | Mapping[str, Any],
    *,
    hidden_holdout: object = None,
) -> LDOExperimentPlan:
    """Validate a public plan and return an immutable independent projection.

    Required deterministic cases must be present and unchanged.  Extra cases
    may be supplied by a future bounded planner, but they receive the same
    strict schema, rail, time, dataset, and budget validation.
    """
    normalized = canonicalize_experiment_policy(policy)
    raw = plan.to_dict() if isinstance(plan, LDOExperimentPlan) else plan
    if not isinstance(raw, Mapping):
        raise LDOExperimentSafetyError("experiment plan must be an object")
    unknown = set(raw) - PLAN_FIELDS
    missing = PLAN_FIELDS - set(raw)
    if unknown or missing:
        raise LDOExperimentSafetyError(
            "experiment plan fields mismatch: missing=%s unknown=%s"
            % (sorted(missing), sorted(str(item) for item in unknown))
        )
    if raw.get("schema_version") != 1 or raw.get("kind") != "ldo-experiment-plan":
        raise LDOExperimentSafetyError("experiment plan schema/kind is unsupported")
    cell = normalize_topology(raw.get("cell"))
    topology = normalize_topology(raw.get("topology"))
    if cell != topology:
        raise LDOExperimentSafetyError("experiment plan cell/topology mismatch")
    if raw.get("policy_id") != normalized.policy_id:
        raise LDOExperimentSafetyError("experiment plan policy_id mismatch")
    if raw.get("policy_digest") != normalized.digest:
        raise LDOExperimentSafetyError("experiment plan policy digest mismatch")
    holdout = raw.get("holdout")
    if holdout != {"locked": True, "public_values_included": False}:
        raise LDOExperimentSafetyError("experiment plan must keep holdout data locked")
    raw_observables = raw.get("observables")
    if not isinstance(raw_observables, list) or tuple(raw_observables) != normalized.observables:
        raise LDOExperimentSafetyError("experiment plan observables differ from policy")

    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise LDOExperimentSafetyError("experiment plan cases must be a non-empty array")
    cases = tuple(case_from_mapping(item) for item in raw_cases)
    case_ids = [item.case_id for item in cases]
    if len(case_ids) != len(set(case_ids)):
        raise LDOExperimentSafetyError("experiment plan case IDs must be unique")
    for case in cases:
        if case.observables != normalized.observables:
            raise LDOExperimentSafetyError(
                "case %s observables differ from policy" % case.case_id
            )
        validate_case_safety(case, topology, normalized)

    expected = {item.case_id: item.to_dict() for item in expected_cases(topology, normalized)}
    actual = {item.case_id: item.to_dict() for item in cases}
    missing_required = sorted(set(expected) - set(actual))
    if missing_required:
        raise LDOExperimentSafetyError(
            "experiment plan lacks required cases: %s" % missing_required
        )
    for case_id, declaration in expected.items():
        if actual[case_id] != declaration:
            raise LDOExperimentSafetyError(
                "required experiment case %s differs from deterministic policy" % case_id
            )
    check_budget(normalized, cases)

    raw_budget = raw.get("budget")
    if not isinstance(raw_budget, Mapping) or set(raw_budget) != PLAN_BUDGET_FIELDS:
        raise LDOExperimentSafetyError("experiment plan budget projection is malformed")
    projected = budget_projection(normalized, cases)
    for name, expected_value in projected.items():
        actual_value = raw_budget.get(name)
        if isinstance(expected_value, float):
            if (
                isinstance(actual_value, bool)
                or not isinstance(actual_value, (int, float))
                or not math.isfinite(float(actual_value))
                or not math.isclose(
                    float(actual_value), expected_value, rel_tol=0.0, abs_tol=1e-15
                )
            ):
                raise LDOExperimentSafetyError(
                    "experiment plan budget projection differs at %s" % name
                )
        elif actual_value != expected_value:
            raise LDOExperimentSafetyError(
                "experiment plan budget projection differs at %s" % name
            )

    safe_plan = LDOExperimentPlan(
        cell,
        topology,
        normalized.policy_id,
        normalized.digest,
        normalized.observables,
        projected,
        cases,
    )
    assert_no_holdout_leak(safe_plan.to_dict(), hidden_holdout)
    return safe_plan


validate_ldo_experiment_plan = validate_experiment_plan
