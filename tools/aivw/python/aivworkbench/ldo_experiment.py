"""Build deterministic LDO experiment plans through their domain owners."""

from __future__ import annotations


from typing import Any, Mapping

from .ldo_experiment_values import normalize_topology
from .ldo_experiment_policy import LDOExperimentPolicy, canonicalize_experiment_policy
from .ldo_experiment_plan import LDOExperimentPlan
from .ldo_experiment_cases import expected_cases
from .ldo_experiment_budget import budget_projection, check_budget
from .ldo_experiment_validation import validate_experiment_plan

def build_experiment_plan(
    topology: str,
    policy: LDOExperimentPolicy | Mapping[str, Any],
    *,
    hidden_holdout: object = None,
) -> LDOExperimentPlan:
    """Build and validate the deterministic public plan for one LDO variant."""
    selected = normalize_topology(topology)
    normalized = canonicalize_experiment_policy(policy)
    cases = expected_cases(selected, normalized)
    check_budget(normalized, cases)
    plan = LDOExperimentPlan(
        selected,
        selected,
        normalized.policy_id,
        normalized.digest,
        normalized.observables,
        budget_projection(normalized, cases),
        cases,
    )
    return validate_experiment_plan(plan, normalized, hidden_holdout=hidden_holdout)


build_ldo_experiment_plan = build_experiment_plan


# Compat exports: remove once external callers use the named owners.
from .ldo_experiment_limits import MAX_EXPERIMENT_CASES as MAX_EXPERIMENT_CASES, MAX_DC_CASES as MAX_DC_CASES, MAX_TRANSIENT_CASES as MAX_TRANSIENT_CASES, MAX_CASE_STOP_TIME_SECONDS as MAX_CASE_STOP_TIME_SECONDS, MAX_TOTAL_STOP_TIME_SECONDS as MAX_TOTAL_STOP_TIME_SECONDS
from .ldo_experiment_errors import LDOExperimentError as LDOExperimentError, LDOExperimentPolicyError as LDOExperimentPolicyError, LDOExperimentSafetyError as LDOExperimentSafetyError, LDOExperimentBudgetError as LDOExperimentBudgetError, LDOHoldoutLeakError as LDOHoldoutLeakError
from .ldo_experiment_case import LDOExperimentCase as LDOExperimentCase
from .ldo_experiment_validation import validate_ldo_experiment_plan as validate_ldo_experiment_plan

__all__ = ['MAX_EXPERIMENT_CASES', 'MAX_DC_CASES', 'MAX_TRANSIENT_CASES', 'MAX_CASE_STOP_TIME_SECONDS', 'MAX_TOTAL_STOP_TIME_SECONDS', 'LDOExperimentError', 'LDOExperimentPolicyError', 'LDOExperimentSafetyError', 'LDOExperimentBudgetError', 'LDOHoldoutLeakError', 'LDOExperimentPolicy', 'LDOExperimentCase', 'LDOExperimentPlan', 'canonicalize_experiment_policy', 'build_experiment_plan', 'validate_experiment_plan', 'build_ldo_experiment_plan', 'validate_ldo_experiment_plan']
