"""Normalize recipe-owned LDO experiment safety policies."""

from __future__ import annotations

from dataclasses import dataclass


from typing import Any, Mapping, Sequence

from .ldo_experiment_limits import MAX_CASE_STOP_TIME_SECONDS, MAX_DC_CASES, MAX_EXPERIMENT_CASES, MAX_TOTAL_STOP_TIME_SECONDS, MAX_TRANSIENT_CASES
from .ldo_experiment_errors import LDOExperimentPolicyError
from .ldo_experiment_values import bounded_count, canonical_digest, finite, freeze, identifier, mapping, positive, thaw

POLICY_FIELDS = {
    "schema_version",
    "policy_id",
    "supply",
    "load",
    "enable",
    "transient",
    "budget",
    "observables",
}


SUPPLY_FIELDS = {
    "ground_v",
    "safe_min_v",
    "safe_max_v",
    "valid_min_v",
    "valid_max_v",
    "nominal_v",
    "out_of_window_margin_v",
}


LOAD_FIELDS = {"safe_min_a", "safe_max_a", "nominal_a"}


ENABLE_FIELDS = {"active_level", "inactive_level", "edge_time_s"}


TRANSIENT_FIELDS = {
    "stop_time_s",
    "startup_ramp_time_s",
    "supply_step_time_s",
    "sample_period_s",
}


BUDGET_FIELDS = {
    "max_cases",
    "max_dc_cases",
    "max_transient_cases",
    "max_total_stop_time_s",
}


@dataclass(frozen=True)
class LDOExperimentPolicy:
    """Immutable normalized projection of a recipe-relative policy manifest."""

    policy_id: str
    supply: Mapping[str, float]
    load: Mapping[str, float]
    enable: Mapping[str, object]
    transient: Mapping[str, float]
    budget: Mapping[str, object]
    observables: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "supply", freeze(self.supply))
        object.__setattr__(self, "load", freeze(self.load))
        object.__setattr__(self, "enable", freeze(self.enable))
        object.__setattr__(self, "transient", freeze(self.transient))
        object.__setattr__(self, "budget", freeze(self.budget))
        object.__setattr__(self, "observables", tuple(self.observables))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "supply": thaw(self.supply),
            "load": thaw(self.load),
            "enable": thaw(self.enable),
            "transient": thaw(self.transient),
            "budget": thaw(self.budget),
            "observables": list(self.observables),
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


def canonicalize_experiment_policy(
    value: LDOExperimentPolicy | Mapping[str, Any],
) -> LDOExperimentPolicy:
    """Validate and normalize the complete experiment policy without mutation."""
    if isinstance(value, LDOExperimentPolicy):
        return value
    root = mapping(value, "experiment policy", POLICY_FIELDS)
    if root.get("schema_version") != 1:
        raise LDOExperimentPolicyError("unsupported experiment policy schema_version")
    policy_id = identifier(root.get("policy_id"), "experiment policy.policy_id")

    supply_raw = mapping(root.get("supply"), "experiment policy.supply", SUPPLY_FIELDS)
    supply = {name: finite(supply_raw[name], "supply.%s" % name) for name in SUPPLY_FIELDS}
    if not supply["safe_min_v"] <= supply["ground_v"] <= supply["safe_max_v"]:
        raise LDOExperimentPolicyError("supply.ground_v is outside the safe voltage range")
    if not (
        supply["safe_min_v"]
        < supply["valid_min_v"]
        < supply["nominal_v"]
        < supply["valid_max_v"]
        < supply["safe_max_v"]
    ):
        raise LDOExperimentPolicyError(
            "supply ranges must satisfy safe_min < valid_min < nominal < valid_max < safe_max"
        )
    margin = supply["out_of_window_margin_v"]
    if margin <= 0.0:
        raise LDOExperimentPolicyError("supply.out_of_window_margin_v must be positive")
    if supply["valid_min_v"] - margin < supply["safe_min_v"]:
        raise LDOExperimentPolicyError("lower out-of-window probe escapes the safe range")
    if supply["valid_max_v"] + margin > supply["safe_max_v"]:
        raise LDOExperimentPolicyError("upper out-of-window probe escapes the safe range")
    if supply["valid_min_v"] < supply["ground_v"]:
        raise LDOExperimentPolicyError("valid VDD must not be below the declared ground rail")

    load_raw = mapping(root.get("load"), "experiment policy.load", LOAD_FIELDS)
    load = {name: finite(load_raw[name], "load.%s" % name) for name in LOAD_FIELDS}
    if not 0.0 <= load["safe_min_a"] < load["nominal_a"] < load["safe_max_a"]:
        raise LDOExperimentPolicyError(
            "load range must satisfy 0 <= safe_min < nominal < safe_max"
        )

    enable_raw = mapping(root.get("enable"), "experiment policy.enable", ENABLE_FIELDS)
    active = enable_raw.get("active_level")
    inactive = enable_raw.get("inactive_level")
    if (
        isinstance(active, bool)
        or isinstance(inactive, bool)
        or active not in {0, 1}
        or inactive not in {0, 1}
        or active == inactive
    ):
        raise LDOExperimentPolicyError(
            "enable active/inactive levels must be distinct integers from {0, 1}"
        )
    enable = {
        "active_level": int(active),
        "inactive_level": int(inactive),
        "edge_time_s": positive(enable_raw.get("edge_time_s"), "enable.edge_time_s"),
    }

    transient_raw = mapping(
        root.get("transient"), "experiment policy.transient", TRANSIENT_FIELDS
    )
    transient = {
        name: positive(transient_raw[name], "transient.%s" % name)
        for name in TRANSIENT_FIELDS
    }
    stop = transient["stop_time_s"]
    if stop > MAX_CASE_STOP_TIME_SECONDS:
        raise LDOExperimentPolicyError(
            "transient.stop_time_s exceeds the hard per-case limit"
        )
    for name in ("startup_ramp_time_s", "supply_step_time_s", "sample_period_s"):
        if transient[name] >= stop:
            raise LDOExperimentPolicyError("transient.%s must be less than stop_time_s" % name)
    if enable["edge_time_s"] >= stop:
        raise LDOExperimentPolicyError("enable.edge_time_s must be less than stop_time_s")

    budget_raw = mapping(root.get("budget"), "experiment policy.budget", BUDGET_FIELDS)
    budget = {
        "max_cases": bounded_count(
            budget_raw.get("max_cases"), "budget.max_cases", MAX_EXPERIMENT_CASES
        ),
        "max_dc_cases": bounded_count(
            budget_raw.get("max_dc_cases"), "budget.max_dc_cases", MAX_DC_CASES
        ),
        "max_transient_cases": bounded_count(
            budget_raw.get("max_transient_cases"),
            "budget.max_transient_cases",
            MAX_TRANSIENT_CASES,
        ),
        "max_total_stop_time_s": positive(
            budget_raw.get("max_total_stop_time_s"), "budget.max_total_stop_time_s"
        ),
    }
    if budget["max_dc_cases"] > budget["max_cases"]:
        raise LDOExperimentPolicyError("budget.max_dc_cases exceeds budget.max_cases")
    if budget["max_transient_cases"] > budget["max_cases"]:
        raise LDOExperimentPolicyError(
            "budget.max_transient_cases exceeds budget.max_cases"
        )
    if budget["max_total_stop_time_s"] > MAX_TOTAL_STOP_TIME_SECONDS:
        raise LDOExperimentPolicyError(
            "budget.max_total_stop_time_s exceeds the hard total-time limit"
        )

    raw_observables = root.get("observables")
    if (
        not isinstance(raw_observables, Sequence)
        or isinstance(raw_observables, (str, bytes))
        or not raw_observables
    ):
        raise LDOExperimentPolicyError("experiment policy.observables must be an array")
    observables = tuple(
        identifier(item, "experiment policy.observable") for item in raw_observables
    )
    if len(observables) != len(set(observables)):
        raise LDOExperimentPolicyError("experiment policy.observables must be unique")
    if "VOUT" not in observables:
        raise LDOExperimentPolicyError("experiment policy must observe VOUT")

    return LDOExperimentPolicy(
        policy_id,
        supply,
        load,
        enable,
        transient,
        budget,
        observables,
    )
