"""Expand recipe trajectories without target-specific equations or ports."""

from copy import deepcopy

from .acceptance import finite
from .electrical_experiment import identifier, validate_experiment


def change_ramp(case, duration_s):
    """Retain a whole single-event case while changing only ramp duration."""
    case = deepcopy(case)
    if len(case["events"]) != 1 or finite(duration_s, "ramp duration") <= 0:
        raise ValueError("one event and a positive ramp duration required")
    event = case["events"][0]
    old_end = event["end_s"]
    event["end_s"] = event["start_s"] + duration_s
    points = (case["load_conductance_pwl_s"] if event["signal"] == "load_conductance"
              else case["voltages"][event["signal"]]["pwl"])
    matches = [p for p in points if p[0] == old_end]
    if len(matches) != 1:
        raise ValueError("ambiguous ramp endpoint")
    matches[0][0] = event["end_s"]
    return case


def expand_public_plan(plan, *, experiment_id, ramp_durations_s, sequential_pairs):
    """Make multi-rate cases and nonoverlapping, two-event sequence cases.

    Simultaneous disturbances need a separate event-metric contract; they are
    not silently interpreted as independent settling measurements here.
    """
    validate_experiment(plan)
    if plan["schema_version"] != 2:
        raise ValueError("dynamic experiment required")
    identifier(experiment_id)
    rates = list(ramp_durations_s)
    if not 2 <= len(rates) <= 8 or len(set(rates)) != len(rates):
        raise ValueError("supply distinct, bounded ramp durations")
    by_id = {c["id"]: c for c in plan["cases"]}
    if len(sequential_pairs) > 16:
        raise ValueError("sequence count exceeds budget")
    result = deepcopy(plan)
    result["experiment_id"] = experiment_id
    result["cases"] = []
    for case in plan["cases"]:
        for index, rate in enumerate(rates):
            value = change_ramp(case, rate)
            value["id"] += "_rate_%d" % index
            result["cases"].append(value)
    # Two original observation intervals allow each event its own windows.
    stop = plan["analysis"]["stop_s"]
    if sequential_pairs:
        result["analysis"]["stop_s"] = 2*stop
        result["analysis"]["steady_window_s"] = [t+stop for t in plan["analysis"]["steady_window_s"]]
        result["analysis"]["sample_times_s"] = sorted(set(
            plan["analysis"]["sample_times_s"]+[t+stop for t in plan["analysis"]["sample_times_s"]]))
        for case in result["cases"]:
            for source in case["voltages"].values():
                if "pwl" in source:
                    source["pwl"].append([2*stop, source["pwl"][-1][1]])
            if case["load_conductance_pwl_s"]:
                case["load_conductance_pwl_s"].append([2*stop, case["load_conductance_pwl_s"][-1][1]])
        for first_id, second_id in sequential_pairs:
            first, second = by_id[first_id], by_id[second_id]
            if len(first["events"]) != 1 or len(second["events"]) != 1:
                raise ValueError("sequence requires single-event source cases")
            case = deepcopy(first)
            case["id"] = first_id+"_then_"+second_id
            case["purpose"] = "sequential_combined_excitation"
            for pin in case["voltages"]:
                a, b = first["voltages"][pin], second["voltages"][pin]
                pa = a.get("pwl", [[0., a.get("dc_v")], [stop, a.get("dc_v")]])
                pb = b.get("pwl", [[0., b.get("dc_v")], [stop, b.get("dc_v")]])
                if pa[-1][1] != pb[0][1]:
                    raise ValueError("sequence voltage states are discontinuous")
                points = deepcopy(pa)+[[t+stop, v] for t, v in pb[1:]]
                case["voltages"][pin] = ({"dc_v": points[0][1]} if len({v for _, v in points}) == 1
                                         else {"pwl": points})
            pa = first["load_conductance_pwl_s"] or [[0., 1/first["load_ohm"]], [stop, 1/first["load_ohm"]]]
            pb = second["load_conductance_pwl_s"] or [[0., 1/second["load_ohm"]], [stop, 1/second["load_ohm"]]]
            if pa[-1][1] != pb[0][1]:
                raise ValueError("sequence load states are discontinuous")
            case["load_conductance_pwl_s"] = deepcopy(pa)+[[t+stop, v] for t, v in pb[1:]]
            event = deepcopy(second["events"][0])
            event["id"] = "second"
            for key in ("start_s", "end_s"):
                event[key] += stop
            for key in ("before_window_s", "after_window_s"):
                event[key] = [t+stop for t in event[key]]
            case["events"].append(event)
            result["cases"].append(case)
    return validate_experiment(result)
