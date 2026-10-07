"""Project and enforce aggregate LDO experiment budgets."""

from __future__ import annotations


import math


from typing import Sequence

from .ldo_experiment_errors import LDOExperimentBudgetError
from .ldo_experiment_policy import LDOExperimentPolicy
from .ldo_experiment_case import LDOExperimentCase

def budget_projection(
    policy: LDOExperimentPolicy, cases: Sequence[LDOExperimentCase]
) -> dict[str, object]:
    dc_count = sum(item.analysis == "dc" for item in cases)
    transient_count = sum(item.analysis == "transient" for item in cases)
    total_stop = math.fsum(item.estimated_stop_time_s for item in cases)
    result = dict(policy.budget)
    result.update(
        {
            "planned_cases": len(cases),
            "planned_dc_cases": dc_count,
            "planned_transient_cases": transient_count,
            "planned_total_stop_time_s": total_stop,
        }
    )
    return result


def check_budget(
    policy: LDOExperimentPolicy, cases: Sequence[LDOExperimentCase]
) -> None:
    projection = budget_projection(policy, cases)
    checks = (
        ("planned_cases", "max_cases"),
        ("planned_dc_cases", "max_dc_cases"),
        ("planned_transient_cases", "max_transient_cases"),
        ("planned_total_stop_time_s", "max_total_stop_time_s"),
    )
    for used_name, limit_name in checks:
        if projection[used_name] > policy.budget[limit_name]:
            raise LDOExperimentBudgetError(
                "%s exceeds %s (%s > %s)"
                % (used_name, limit_name, projection[used_name], policy.budget[limit_name])
            )
