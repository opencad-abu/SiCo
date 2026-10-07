"""Generate required deterministic calibration cases from one policy."""

from __future__ import annotations


from .ldo_experiment_policy import LDOExperimentPolicy
from .ldo_experiment_case import LDOExperimentCase

def dc_case(
    case_id: str,
    purpose: str,
    policy: LDOExperimentPolicy,
    topology: str,
    *,
    vdd_v: float,
    load_a: float,
    en: int | None = None,
) -> LDOExperimentCase:
    stimulus: dict[str, object] = {
        "vdd_v": vdd_v,
        "vss_v": policy.supply["ground_v"],
        "load_a": load_a,
    }
    if topology == "LDO_AON":
        stimulus["en"] = policy.enable["active_level"] if en is None else en
    return LDOExperimentCase(
        case_id,
        "dc",
        purpose,
        stimulus,
        policy.observables,
        0.0,
    )


def vdd_transient_case(
    case_id: str,
    purpose: str,
    policy: LDOExperimentPolicy,
    topology: str,
    *,
    initial_vdd_v: float,
    final_vdd_v: float,
    load_a: float,
    transition_time_s: float,
) -> LDOExperimentCase:
    stimulus: dict[str, object] = {
        "initial_vdd_v": initial_vdd_v,
        "final_vdd_v": final_vdd_v,
        "vss_v": policy.supply["ground_v"],
        "load_a": load_a,
        "transition_time_s": transition_time_s,
        "stop_time_s": policy.transient["stop_time_s"],
        "sample_period_s": policy.transient["sample_period_s"],
    }
    if topology == "LDO_AON":
        stimulus["en"] = policy.enable["active_level"]
    return LDOExperimentCase(
        case_id,
        "transient",
        purpose,
        stimulus,
        policy.observables,
        policy.transient["stop_time_s"],
    )


def enable_transient_case(
    case_id: str,
    purpose: str,
    policy: LDOExperimentPolicy,
    *,
    initial_en: int,
    final_en: int,
) -> LDOExperimentCase:
    stimulus = {
        "vdd_v": policy.supply["nominal_v"],
        "vss_v": policy.supply["ground_v"],
        "load_a": policy.load["nominal_a"],
        "initial_en": initial_en,
        "final_en": final_en,
        "transition_time_s": policy.enable["edge_time_s"],
        "stop_time_s": policy.transient["stop_time_s"],
        "sample_period_s": policy.transient["sample_period_s"],
    }
    return LDOExperimentCase(
        case_id,
        "transient",
        purpose,
        stimulus,
        policy.observables,
        policy.transient["stop_time_s"],
    )


def expected_cases(
    topology: str, policy: LDOExperimentPolicy
) -> tuple[LDOExperimentCase, ...]:
    supply = policy.supply
    load = policy.load
    enabled = policy.enable["active_level"]
    midpoint = (supply["valid_min_v"] + supply["valid_max_v"]) / 2.0
    cases = [
        dc_case(
            "dc-supply-below-window",
            "supply_below_valid_window",
            policy,
            topology,
            vdd_v=supply["valid_min_v"] - supply["out_of_window_margin_v"],
            load_a=load["nominal_a"],
        ),
        dc_case(
            "dc-supply-valid-minimum",
            "supply_valid_minimum",
            policy,
            topology,
            vdd_v=supply["valid_min_v"],
            load_a=load["nominal_a"],
        ),
        dc_case(
            "dc-supply-nominal",
            "nominal_operating_point",
            policy,
            topology,
            vdd_v=supply["nominal_v"],
            load_a=load["nominal_a"],
        ),
        dc_case(
            "dc-supply-midpoint",
            "supply_window_midpoint",
            policy,
            topology,
            vdd_v=midpoint,
            load_a=load["nominal_a"],
        ),
        dc_case(
            "dc-supply-valid-maximum",
            "supply_valid_maximum",
            policy,
            topology,
            vdd_v=supply["valid_max_v"],
            load_a=load["nominal_a"],
        ),
        dc_case(
            "dc-supply-above-window",
            "supply_above_valid_window",
            policy,
            topology,
            vdd_v=supply["valid_max_v"] + supply["out_of_window_margin_v"],
            load_a=load["nominal_a"],
        ),
        dc_case(
            "dc-load-minimum",
            "load_boundary_minimum",
            policy,
            topology,
            vdd_v=supply["nominal_v"],
            load_a=load["safe_min_a"],
        ),
        dc_case(
            "dc-load-maximum",
            "load_boundary_maximum",
            policy,
            topology,
            vdd_v=supply["nominal_v"],
            load_a=load["safe_max_a"],
        ),
    ]
    if topology == "LDO_AON":
        cases.extend(
            [
                dc_case(
                    "dc-enable-inactive",
                    "enable_inactive",
                    policy,
                    topology,
                    vdd_v=supply["nominal_v"],
                    load_a=load["nominal_a"],
                    en=policy.enable["inactive_level"],
                ),
                dc_case(
                    "dc-enable-active",
                    "enable_active",
                    policy,
                    topology,
                    vdd_v=supply["nominal_v"],
                    load_a=load["nominal_a"],
                    en=enabled,
                ),
            ]
        )
    cases.extend(
        [
            vdd_transient_case(
                "tran-vdd-startup-nominal-load",
                "vdd_startup_nominal_load",
                policy,
                topology,
                initial_vdd_v=supply["ground_v"],
                final_vdd_v=supply["nominal_v"],
                load_a=load["nominal_a"],
                transition_time_s=policy.transient["startup_ramp_time_s"],
            ),
            vdd_transient_case(
                "tran-vdd-startup-boundary-load",
                "vdd_startup_boundary_load",
                policy,
                topology,
                initial_vdd_v=supply["ground_v"],
                final_vdd_v=supply["nominal_v"],
                load_a=load["safe_max_a"],
                transition_time_s=policy.transient["startup_ramp_time_s"],
            ),
            vdd_transient_case(
                "tran-vdd-step-down",
                "vdd_step_down",
                policy,
                topology,
                initial_vdd_v=supply["nominal_v"],
                final_vdd_v=supply["valid_min_v"],
                load_a=load["nominal_a"],
                transition_time_s=policy.transient["supply_step_time_s"],
            ),
            vdd_transient_case(
                "tran-vdd-step-up",
                "vdd_step_up",
                policy,
                topology,
                initial_vdd_v=supply["nominal_v"],
                final_vdd_v=supply["valid_max_v"],
                load_a=load["nominal_a"],
                transition_time_s=policy.transient["supply_step_time_s"],
            ),
        ]
    )
    if topology == "LDO_AON":
        cases.extend(
            [
                enable_transient_case(
                    "tran-enable-rise",
                    "enable_inactive_to_active",
                    policy,
                    initial_en=policy.enable["inactive_level"],
                    final_en=enabled,
                ),
                enable_transient_case(
                    "tran-enable-fall",
                    "enable_active_to_inactive",
                    policy,
                    initial_en=enabled,
                    final_en=policy.enable["inactive_level"],
                ),
            ]
        )
    return tuple(cases)
