from __future__ import annotations

from copy import deepcopy
import math

import pytest

from aivworkbench.ldo_experiment import (
    LDOExperimentBudgetError,
    LDOExperimentPolicyError,
    LDOExperimentSafetyError,
    LDOHoldoutLeakError,
    build_experiment_plan,
    canonicalize_experiment_policy,
    validate_experiment_plan,
)


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_id": "ldo-controlled-fixture",
        "supply": {
            "ground_v": 0.0,
            "safe_min_v": 0.0,
            "safe_max_v": 6.0,
            "valid_min_v": 4.5,
            "valid_max_v": 5.5,
            "nominal_v": 5.0,
            "out_of_window_margin_v": 0.25,
        },
        "load": {
            "safe_min_a": 0.0,
            "safe_max_a": 0.01,
            "nominal_a": 0.005,
        },
        "enable": {
            "active_level": 1,
            "inactive_level": 0,
            "edge_time_s": 1.0e-6,
        },
        "transient": {
            "stop_time_s": 1.0e-3,
            "startup_ramp_time_s": 1.0e-4,
            "supply_step_time_s": 1.0e-5,
            "sample_period_s": 1.0e-6,
        },
        "budget": {
            "max_cases": 32,
            "max_dc_cases": 16,
            "max_transient_cases": 16,
            "max_total_stop_time_s": 0.1,
        },
        "observables": ["VOUT", "IDD", "operating_point"],
    }


@pytest.mark.parametrize(
    ("topology", "case_count", "dc_count", "transient_count"),
    (("LDO_MASTER", 12, 8, 4), ("LDO_AON", 16, 10, 6)),
)
def test_builds_bounded_deterministic_plan(
    topology: str, case_count: int, dc_count: int, transient_count: int
) -> None:
    source = _policy()
    original = deepcopy(source)

    first = build_experiment_plan(topology, source)
    second = build_experiment_plan(topology, source)

    assert source == original
    assert first.digest == second.digest
    assert len(first.cases) == case_count
    assert first.budget["planned_dc_cases"] == dc_count
    assert first.budget["planned_transient_cases"] == transient_count
    assert first.to_dict()["holdout"] == {
        "locked": True,
        "public_values_included": False,
    }
    for case in first.cases:
        assert case.dataset == "calibration"
        assert math.isfinite(case.estimated_stop_time_s)
        assert 0.0 <= case.estimated_stop_time_s <= 1.0e-3
        assert 0.0 <= case.stimulus.get(
            "vdd_v", case.stimulus.get("initial_vdd_v", 0.0)
        ) <= 6.0
        assert 0.0 <= case.stimulus.get(
            "vdd_v", case.stimulus.get("final_vdd_v", 0.0)
        ) <= 6.0
        assert 0.0 <= case.stimulus["load_a"] <= 0.01


def test_enable_cases_are_topology_specific() -> None:
    master = build_experiment_plan("LDO_MASTER", _policy()).to_dict()
    aon = build_experiment_plan("LDO_AON", _policy()).to_dict()

    assert all(
        not any(name in case["stimulus"] for name in ("en", "initial_en", "final_en"))
        for case in master["cases"]
    )
    assert {case["id"] for case in aon["cases"]}.issuperset(
        {
            "dc-enable-inactive",
            "dc-enable-active",
            "tran-enable-rise",
            "tran-enable-fall",
        }
    )


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("supply", "nominal_v"), float("nan")),
        (("supply", "safe_max_v"), float("inf")),
        (("load", "safe_min_a"), -1.0),
        (("transient", "stop_time_s"), -1.0),
    ),
)
def test_policy_rejects_nonfinite_or_inverted_domains(
    path: tuple[str, str], value: float
) -> None:
    policy = _policy()
    policy[path[0]][path[1]] = value

    with pytest.raises(LDOExperimentPolicyError):
        canonicalize_experiment_policy(policy)


def test_budget_exhaustion_is_explicit() -> None:
    policy = _policy()
    policy["budget"]["max_cases"] = 10
    policy["budget"]["max_dc_cases"] = 10
    policy["budget"]["max_transient_cases"] = 10

    with pytest.raises(LDOExperimentBudgetError, match="planned_cases"):
        build_experiment_plan("LDO_MASTER", policy)


def test_validator_rejects_unsafe_tampering_and_unknown_fields() -> None:
    policy = _policy()
    plan = build_experiment_plan("LDO_MASTER", policy).to_dict()
    plan["cases"][0]["stimulus"]["vdd_v"] = 6.1
    with pytest.raises(LDOExperimentSafetyError, match="safe rail"):
        validate_experiment_plan(plan, policy)

    plan = build_experiment_plan("LDO_MASTER", policy).to_dict()
    plan["cases"][0]["stimulus"]["command"] = "spectre"
    with pytest.raises(LDOExperimentSafetyError, match="stimulus fields mismatch"):
        validate_experiment_plan(plan, policy)


def test_validator_rejects_hidden_holdout_id_or_value() -> None:
    policy = _policy()
    public = build_experiment_plan("LDO_MASTER", policy)

    with pytest.raises(LDOHoldoutLeakError, match="identifier leaked"):
        validate_experiment_plan(
            public,
            policy,
            hidden_holdout={"case_ids": ["dc-supply-nominal"]},
        )
    with pytest.raises(LDOHoldoutLeakError, match="value leaked"):
        validate_experiment_plan(
            public,
            policy,
            hidden_holdout={"values": [4.25]},
        )


def test_plan_is_recursively_immutable_and_to_dict_is_independent() -> None:
    plan = build_experiment_plan("LDO_AON", _policy())
    with pytest.raises(TypeError):
        plan.cases[0].stimulus["vdd_v"] = 1.0

    exported = plan.to_dict()
    exported["cases"][0]["stimulus"]["vdd_v"] = 1.0
    assert plan.to_dict()["cases"][0]["stimulus"]["vdd_v"] == 4.25


def test_unsupported_topology_fails_closed() -> None:
    with pytest.raises(LDOExperimentPolicyError, match="unsupported LDO topology"):
        build_experiment_plan("OTHER_LDO", _policy())
