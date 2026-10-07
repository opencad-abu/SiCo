"""Immutable public LDO experiment plan schema."""

from __future__ import annotations

from dataclasses import dataclass


from typing import Mapping

from .ldo_experiment_values import canonical_digest, freeze, thaw
from .ldo_experiment_policy import BUDGET_FIELDS
from .ldo_experiment_case import LDOExperimentCase

PLAN_FIELDS = {
    "schema_version",
    "kind",
    "cell",
    "topology",
    "policy_id",
    "policy_digest",
    "holdout",
    "observables",
    "budget",
    "cases",
}


PLAN_BUDGET_FIELDS = BUDGET_FIELDS | {
    "planned_cases",
    "planned_dc_cases",
    "planned_transient_cases",
    "planned_total_stop_time_s",
}


@dataclass(frozen=True)
class LDOExperimentPlan:
    """Validated public plan; ``to_dict`` returns a fully independent copy."""

    cell: str
    topology: str
    policy_id: str
    policy_digest: str
    observables: tuple[str, ...]
    budget: Mapping[str, object]
    cases: tuple[LDOExperimentCase, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "observables", tuple(self.observables))
        object.__setattr__(self, "budget", freeze(self.budget))
        object.__setattr__(self, "cases", tuple(self.cases))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "kind": "ldo-experiment-plan",
            "cell": self.cell,
            "topology": self.topology,
            "policy_id": self.policy_id,
            "policy_digest": self.policy_digest,
            "holdout": {"locked": True, "public_values_included": False},
            "observables": list(self.observables),
            "budget": thaw(self.budget),
            "cases": [item.to_dict() for item in self.cases],
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())
