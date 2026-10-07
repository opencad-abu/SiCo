"""Enforce rail, load, logic-level and timing safety for one case."""

from __future__ import annotations


import math


from .ldo_experiment_limits import MAX_CASE_STOP_TIME_SECONDS
from .ldo_experiment_errors import LDOExperimentPolicyError, LDOExperimentSafetyError
from .ldo_experiment_values import finite
from .ldo_experiment_policy import LDOExperimentPolicy
from .ldo_experiment_case import LDOExperimentCase

def safety_number(value: object, label: str) -> float:
    try:
        return finite(value, label)
    except LDOExperimentPolicyError as exc:
        raise LDOExperimentSafetyError(str(exc)) from exc


def validate_level(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in {0, 1}:
        raise LDOExperimentSafetyError("%s must be integer 0 or 1" % label)
    return value


def validate_case_safety(
    case: LDOExperimentCase,
    topology: str,
    policy: LDOExperimentPolicy,
) -> None:
    stimulus = case.stimulus
    ground = policy.supply["ground_v"]
    safe_min = policy.supply["safe_min_v"]
    safe_max = policy.supply["safe_max_v"]
    load_min = policy.load["safe_min_a"]
    load_max = policy.load["safe_max_a"]
    allowed_dc = {"vdd_v", "vss_v", "load_a"}
    allowed_vdd_transient = {
        "initial_vdd_v",
        "final_vdd_v",
        "vss_v",
        "load_a",
        "transition_time_s",
        "stop_time_s",
        "sample_period_s",
    }
    allowed_enable_transient = {
        "vdd_v",
        "vss_v",
        "load_a",
        "initial_en",
        "final_en",
        "transition_time_s",
        "stop_time_s",
        "sample_period_s",
    }
    if topology == "LDO_AON":
        allowed_dc.add("en")
        allowed_vdd_transient.add("en")

    if case.analysis == "dc":
        allowed = allowed_dc
    elif "initial_en" in stimulus or "final_en" in stimulus:
        allowed = allowed_enable_transient
        if topology != "LDO_AON":
            raise LDOExperimentSafetyError("LDO_MASTER cannot receive enable stimuli")
    else:
        allowed = allowed_vdd_transient
    if set(stimulus) != allowed:
        raise LDOExperimentSafetyError(
            "case %s stimulus fields mismatch: expected=%s actual=%s"
            % (case.case_id, sorted(allowed), sorted(str(item) for item in stimulus))
        )

    vss = safety_number(stimulus.get("vss_v"), "%s.vss_v" % case.case_id)
    if vss != ground:
        raise LDOExperimentSafetyError("case %s changes the declared ground rail" % case.case_id)
    load = safety_number(stimulus.get("load_a"), "%s.load_a" % case.case_id)
    if not load_min <= load <= load_max:
        raise LDOExperimentSafetyError("case %s load escapes the safe range" % case.case_id)

    voltage_names = (
        ("vdd_v",)
        if "vdd_v" in stimulus
        else ("initial_vdd_v", "final_vdd_v")
    )
    for name in voltage_names:
        voltage = safety_number(stimulus.get(name), "%s.%s" % (case.case_id, name))
        if not safe_min <= voltage <= safe_max or voltage < ground:
            raise LDOExperimentSafetyError(
                "case %s voltage %s escapes the safe rail domain" % (case.case_id, name)
            )

    for name in ("en", "initial_en", "final_en"):
        if name in stimulus:
            validate_level(stimulus[name], "%s.%s" % (case.case_id, name))

    if case.analysis == "dc":
        if case.estimated_stop_time_s != 0.0:
            raise LDOExperimentSafetyError("DC case %s must have zero stop time" % case.case_id)
        return
    transition = safety_number(
        stimulus.get("transition_time_s"), "%s.transition_time_s" % case.case_id
    )
    stop = safety_number(stimulus.get("stop_time_s"), "%s.stop_time_s" % case.case_id)
    sample = safety_number(
        stimulus.get("sample_period_s"), "%s.sample_period_s" % case.case_id
    )
    if transition <= 0.0 or stop <= 0.0 or sample <= 0.0:
        raise LDOExperimentSafetyError("case %s transient times must be positive" % case.case_id)
    if transition >= stop or sample >= stop:
        raise LDOExperimentSafetyError(
            "case %s transition/sample period must be below stop time" % case.case_id
        )
    if stop > policy.transient["stop_time_s"] or stop > MAX_CASE_STOP_TIME_SECONDS:
        raise LDOExperimentSafetyError("case %s stop time exceeds policy" % case.case_id)
    if not math.isclose(case.estimated_stop_time_s, stop, rel_tol=0.0, abs_tol=1e-15):
        raise LDOExperimentSafetyError(
            "case %s estimated duration does not match stimulus stop time" % case.case_id
        )
