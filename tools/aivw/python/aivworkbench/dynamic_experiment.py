"""Version 2 electrical trajectories, including a continuously varying load.

Load conductance is an environment stimulus, not an invented source terminal.
Every ramp is explicit and every event window is bounded by its trajectory.
"""

from __future__ import annotations

from copy import deepcopy
import json

from .acceptance import finite


def base_experiment(value):
    base = deepcopy(value)
    base["schema_version"] = 1
    base.pop("measurement", None)
    for case in base["cases"]:
        case.pop("load_conductance_pwl_s", None)
        case.pop("events", None)
    return base


def validate_dynamic_experiment(value):
    from .electrical_experiment import identifier, validate_experiment
    if type(value.get("schema_version")) is not int or value["schema_version"] != 2:
        raise ValueError("unsupported dynamic experiment version")
    validate_experiment(base_experiment(value))
    measurement = value["measurement"]
    if set(measurement) != {"sample_step_s", "settling_relative_band", "settling_absolute_band_v", "minimum_dwell_s"}:
        raise ValueError("dynamic measurement fields differ")
    for key, number in measurement.items():
        if finite(number, key) <= 0:
            raise ValueError("dynamic measurement controls must be positive")
    stop = value["analysis"]["stop_s"]
    if stop / measurement["sample_step_s"] > 200000 or measurement["sample_step_s"] > value["analysis"]["maxstep_s"]:
        raise ValueError("dynamic sampling resolution exceeds budget")
    if measurement["settling_relative_band"] >= 1 or measurement["minimum_dwell_s"] >= stop:
        raise ValueError("dynamic settling definition is invalid")
    for case in value["cases"]:
        if case["kind"] != "transient" or case["product_check"]:
            raise ValueError("dynamic trajectories cannot use the nominal DC product gate")
        points = case["load_conductance_pwl_s"]
        if points is not None:
            _validate_load(points, stop, case["load_ohm"])
        events = case["events"]
        if not isinstance(events, list) or not 1 <= len(events) <= 16:
            raise ValueError("dynamic events must be explicit and bounded")
        transitions = {}
        for signal, source in case["voltages"].items():
            for a, b in zip(source.get("pwl", []), source.get("pwl", [])[1:]):
                if a[1] != b[1]:
                    transitions[(signal, a[0], b[0])] = (a[1], b[1])
        for a, b in zip(points or [], (points or [])[1:]):
            if a[1] != b[1]:
                transitions[("load_conductance", a[0], b[0])] = (a[1], b[1])
        identities = set()
        for event in events:
            if set(event) != {"id", "signal", "start_s", "end_s", "before_window_s", "after_window_s"}:
                raise ValueError("dynamic event fields differ")
            identifier(event["id"])
            key = (event["signal"], event["start_s"], event["end_s"])
            if key not in transitions or key in identities:
                raise ValueError("event does not identify one physical ramp")
            identities.add(key)
            before, after = event["before_window_s"], event["after_window_s"]
            for window in (before, after):
                if not isinstance(window, list) or len(window) != 2:
                    raise ValueError("event observation window is invalid")
                for t in window:
                    finite(t, "window time")
            if not 0 <= before[0] < before[1] <= event["start_s"] < event["end_s"] <= after[0] < after[1] <= stop:
                raise ValueError("event windows do not bracket its ramp")
            if after[1] - event["end_s"] < measurement["minimum_dwell_s"]:
                raise ValueError("event has insufficient settling observation time")
            if any(other != key and event["start_s"] <= other[1] < after[1] for other in transitions):
                raise ValueError("overlapping event response windows are unsupported")
        if identities != set(transitions) or len({e["id"] for e in events}) != len(events):
            raise ValueError("dynamic event coverage is incomplete or duplicated")
    if "transient_resolution" in value["analysis"]:
        from .transient_resolution import schedule
        for case in value["cases"]:schedule(value["analysis"]["transient_resolution"],value["analysis"],case["events"])
    return json.loads(json.dumps(value, allow_nan=False))


def _validate_load(points, stop, initial_resistance):
    if not isinstance(points, list) or not 2 <= len(points) <= 64:
        raise ValueError("load trajectory must have bounded points")
    for p in points:
        if not isinstance(p, list) or len(p) != 2:
            raise ValueError("load point must be [time_s, conductance_s]")
        if finite(p[0], "load time") < 0 or finite(p[1], "conductance") <= 0:
            raise ValueError("load conductance must be strictly positive")
    if points[0][0] != 0 or points[-1][0] != stop or any(b[0] <= a[0] for a, b in zip(points, points[1:])):
        raise ValueError("load trajectory must cover the experiment")
    if abs(points[0][1] * initial_resistance - 1) > 1e-12:
        raise ValueError("initial resistance differs from load trajectory")


def render_dynamic_deck(value, case_id):
    from .electrical_experiment import render_deck
    plan = validate_dynamic_experiment(value)
    base = base_experiment(plan)
    deck = render_deck(base, case_id)
    case = next(c for c in plan["cases"] if c["id"] == case_id)
    if "transient_resolution" in plan["analysis"]:
        from .transient_resolution import render
        lines=deck.splitlines()
        lines=[line+render(plan["analysis"]["transient_resolution"],plan["analysis"],case["events"]) if line.startswith("tran tran ") else line for line in lines]
        deck="\n".join(lines)+"\n"
    if case["load_conductance_pwl_s"] is None:
        return deck
    roles = plan["roles"]
    node = "aivw_internal_load_g"
    if node in plan["target"]["term_order"]:
        raise ValueError("load control node collides with source terminal")
    old = "RLOAD (%s %s) resistor r=%.17g" % (roles["output"], roles["reference"], case["load_ohm"])
    wave = " ".join("%.17g %.17g" % tuple(p) for p in case["load_conductance_pwl_s"])
    new = ("VLCTRL (%s 0) vsource type=pwl wave=[ %s ]\n" % (node, wave) +
           "BLOAD (%s %s) bsource i=v(%s,%s)*v(%s)" %
           (roles["output"], roles["reference"], roles["output"], roles["reference"], node))
    if deck.count(old) != 1:
        raise ValueError("load declaration is ambiguous")
    return deck.replace(old, new)
