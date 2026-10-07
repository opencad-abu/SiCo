"""Public-data identification of an additive nonlinear sampled state model.

This is a model-class hypothesis, not an equilibrium measurement or circuit
law. Input-coordinate splines and stable first-order states contain no case
IDs or absolute time. New trajectories must be evaluated independently.
"""

from __future__ import annotations

import math

from ..electrical_experiment import identifier
from ..workspace import stable_digest


def interpolate(points, x):
    if x <= points[0][0]:
        return points[0][1]
    for a, b in zip(points, points[1:]):
        if x <= b[0]:
            return a[1] + (x-a[0])*(b[1]-a[1])/(b[0]-a[0])
    return points[-1][1]


def inputs_at(plan, case, t):
    values = []
    for name in [plan["roles"]["supply"], *plan["roles"]["controls"]]:
        source = case["voltages"][name]
        values.append(source["dc_v"] if "dc_v" in source else interpolate(source["pwl"], t))
    load = case["load_conductance_pwl_s"]
    values.append(1/case["load_ohm"] if load is None else interpolate(load, t))
    return values


def _hat(value, knots, i):
    if value == knots[i]:
        return 1.
    if i and knots[i-1] < value < knots[i]:
        return (value-knots[i-1])/(knots[i]-knots[i-1])
    if i+1 < len(knots) and knots[i] < value < knots[i+1]:
        return (knots[i+1]-value)/(knots[i+1]-knots[i])
    return 0.


def features(model, values, previous, dt):
    result = [1.]
    clamped = []
    for value, axis in zip(values, model["axes"]):
        lo, hi = axis["knots"][0], axis["knots"][-1]
        if not lo-1e-12*max(1., abs(lo)) <= value <= hi+1e-12*max(1., abs(hi)):
            raise ValueError("outside identified input domain")
        value = min(hi, max(lo, value))
        clamped.append(value)
        # Omit the last hat per axis to fix the additive intercept ambiguity.
        result.extend(_hat(value, axis["knots"], i) for i in range(len(axis["knots"])-1))
    for value, old, axis in zip(clamped, previous, model["axes"]):
        slew = (value-old)/dt/axis["slew_scale_per_s"]
        mode = model["identification_options"].get("slew_features", "global")
        if mode == "signed_polynomial":
            for direction in (1., -1.):
                for power in (.5, 1., 2.):
                    amplitude = max(0., direction*slew)**power
                    result.extend(amplitude*_hat(value, axis["knots"], i) for i in range(len(axis["knots"])))
        elif mode == "local":
            result.extend(slew*_hat(value, axis["knots"], i) for i in range(len(axis["knots"])))
        else:
            result.append(slew)
    return result


def _solve(matrix, rhs):
    """Small pivoted, regularized normal system; no optional numerical stack."""
    n = len(rhs)
    aug = [list(row)+[value] for row, value in zip(matrix, rhs)]
    for i in range(n):
        pivot = max(range(i, n), key=lambda j: abs(aug[j][i]))
        aug[i], aug[pivot] = aug[pivot], aug[i]
        if abs(aug[i][i]) < 1e-20:
            raise ValueError("identification basis is singular")
        scale = aug[i][i]
        aug[i] = [v/scale for v in aug[i]]
        for j in range(i+1, n):
            scale = aug[j][i]
            if scale:
                for k in range(i, n+1):
                    aug[j][k] -= scale*aug[i][k]
    answer = [0.]*n
    for i in reversed(range(n)):
        answer[i] = aug[i][-1] - sum(aug[i][j]*answer[j] for j in range(i+1, n))
    return answer


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def identify(request):
    plan, evidence, waves, options = request
    knots_count = options["knots_per_voltage_axis"]
    dt = options["step_s"]
    taus = options["time_constants_s"]
    if set(options) - {"step_s", "knots_per_voltage_axis", "time_constants_s", "slew_features", "memory_time_constants_s"} or options.get("slew_features", "global") not in {"global", "local", "signed_polynomial"}:
        raise ValueError("unsupported identification features")
    if "memory_time_constants_s" in options:
        from .state_memory import validate_constants
        validate_constants(options["memory_time_constants_s"], dt, plan["analysis"]["stop_s"])
    if type(knots_count) is not int or not 3 <= knots_count <= 65 or not 0 < dt <= plan["measurement"]["sample_step_s"]:
        raise ValueError("identification resolution outside bounds")
    if not taus or len(taus) > 20 or any(not math.isfinite(t) or t < 0 or t > plan["analysis"]["stop_s"]/4 for t in taus):
        raise ValueError("invalid stable state time constants")
    if plan["analysis"]["stop_s"]/dt > 100000:
        raise ValueError("state sample budget exceeded")
    names = [plan["roles"]["supply"], *plan["roles"]["controls"], "load_conductance"]
    anchors = [inputs_at(plan, case, t) for case in plan["cases"]
               for t in [0., plan["analysis"]["stop_s"]]]
    axes = []
    for i, name in enumerate(names):
        lo, hi = min(v[i] for v in anchors), max(v[i] for v in anchors)
        if hi <= lo:
            raise ValueError("each identified input must be excited")
        n = 3 if i == len(names)-1 else knots_count
        knots = sorted(set([lo+(hi-lo)*j/(n-1) for j in range(n)] + [v[i] for v in anchors]))
        slew = max(abs(inputs_at(plan, c, e["end_s"])[i]-inputs_at(plan, c, e["start_s"])[i])/(e["end_s"]-e["start_s"])
                   for c in plan["cases"] for e in c["events"])
        axes.append({"name": name, "unit": "S" if i == len(names)-1 else "V", "knots": knots,
                     "slew_scale_per_s": slew})
    model = {"schema_version": 1, "model_class": "electrical.sampled_state", "version": 1,
             "module": identifier(plan["target"]["cell"]), "ports": plan["target"]["term_order"],
             "roles": plan["roles"], "axes": axes, "step_s": dt, "channels": {},
             "source_generation": evidence["source_generation"], "experiment_digest": evidence["experiment_digest"],
             "identification_options": options, "dataset": "public_calibration", "qualification": "NOT_ESTABLISHED",
             "generation_mode": "deterministic_system_identification",
             "load_boundary": "environment_conductance_property_not_oa_terminal",
             "current_boundary": "internal_diagnostic_not_resolved_supply_current",
             "scope": "sampled_additive_nonlinearity_with_first_order_state",
             "unsupported": ["unvalidated_combinations", "outside_input_domain", "different_pvt", "long_term_drift",
                             "bidirectional_electrical_network", "unvalidated_slew_rates"]}
    rows = []
    output = plan["roles"]["output"]
    for case in plan["cases"]:
        wave = waves[case["id"]]
        # Include every transition and its tail, plus independent baseline/end
        # points; avoid overwhelming the excitation with repeated DC samples.
        ts = {i*plan["analysis"]["stop_s"]/100 for i in range(101)}
        for event in case["events"]:
            lo = max(0, event["start_s"]-5*dt)
            hi = min(plan["analysis"]["stop_s"], event["end_s"]+20e-6)
            ts.update(i*dt for i in range(math.ceil(lo/dt), math.floor(hi/dt)+1))
        for t in sorted(ts):
            prev_t = max(0., t-dt)
            values = inputs_at(plan, case, t)
            phi = features(model, values, inputs_at(plan, case, prev_t), dt)
            weight = 10000. if t in (0., plan["analysis"]["stop_s"]) else 1.
            rows.append((phi, [wave.sample(output, t), wave.sample("VPROBE:p", t)],
                         [wave.sample(output, prev_t), wave.sample("VPROBE:p", prev_t)], weight))
    n = len(rows[0][0])
    matrix = [[0.]*n for _ in range(n)]
    for phi, _, _, weight in rows:
        active = [(i, v) for i, v in enumerate(phi) if v]
        for i, v in active:
            for j, w in active:
                matrix[i][j] += weight*v*w
    for i in range(n):
        matrix[i][i] += 1e-8
    hypotheses = []
    for channel, index, unit in (("output", 0, "V"), ("current", 1, "A")):
        for tau in taus:
            a = math.exp(-dt/tau) if tau else 0.
            rhs = [0.]*n
            for phi, now, previous, weight in rows:
                target = (now[index]-a*previous[index])/(1-a)
                for j, value in enumerate(phi):
                    if value:
                        rhs[j] += weight*value*target
            if options.get("slew_features") == "signed_polynomial":
                from .sparse_fit import solve_positive_system
                coeff = solve_positive_system(matrix, rhs)
            else:
                coeff = _solve(matrix, rhs)
            hypotheses.append({"channel": channel, "index": index, "unit": unit,
                               "tau": tau, "a": a, "coeff": coeff, "error": 0.})
    # Rank every complete closed-loop rollout, sharing only input features
    # and golden interpolation across hypotheses. Keep each state independent.
    # Sparse spline support avoids multiplying hundreds of exact zero terms.
    count = 0
    for case in plan["cases"]:
        previous = inputs_at(plan, case, 0.)
        wave = waves[case["id"]]
        for i in range(round(plan["analysis"]["stop_s"]/dt)+1):
            t = i*dt
            values = inputs_at(plan, case, t)
            active = [(j, v) for j, v in enumerate(features(model, values, previous, dt)) if v]
            targets = [wave.sample(output, t), wave.sample("VPROBE:p", t)]
            for h in hypotheses:
                goal = sum(v*h["coeff"][j] for j, v in active)
                h["state"] = h["a"]*h["state"]+(1-h["a"])*goal if i else goal
                h["error"] += (h["state"]-targets[h["index"]])**2
            count += 1
            previous = values
    for channel in ("output", "current"):
        best = min((h for h in hypotheses if h["channel"] == channel), key=lambda h: h["error"])
        model["channels"][channel] = {"unit": best["unit"], "tau_s": best["tau"],
            "coefficients": best["coeff"], "training_rollout_rmse": math.sqrt(best["error"]/count)}
    if "memory_time_constants_s" in options:
        from .state_memory import fit_memory
        fit_memory(model, plan, waves)
    model["model_digest"] = stable_digest(model)
    return model


def simulate(model, plan, case):
    from ..waveform_metrics import Waveform
    stop, dt = plan["analysis"]["stop_s"], model["step_s"]
    count = round(stop/dt)
    if not math.isclose(count*dt, stop, rel_tol=1e-12):
        raise ValueError("stop must be an integer model step")
    axes, outs, currents = [], [], []
    previous = inputs_at(plan, case, 0.)
    memory = None
    if "memory" in model:
        from .state_memory import advance_memory, memory_values
        memory = [0.] * len(model["memory"]["coefficients"]["output"])
    state = {k: _dot(features(model, previous, previous, dt), v["coefficients"])
             for k, v in model["channels"].items()}
    for i in range(count+1):
        t = i*dt
        values = inputs_at(plan, case, t)
        if i:
            phi = features(model, values, previous, dt)
            for k, v in model["channels"].items():
                a = math.exp(-dt/v["tau_s"]) if v["tau_s"] else 0.
                state[k] = a*state[k]+(1-a)*_dot(phi, v["coefficients"])
        axes.append(t)
        correction = {"output": 0., "current": 0.}
        if memory is not None:
            advance_memory(model, memory, values, previous, initialize=(i == 0))
            correction = memory_values(model, memory)
        output_value = state["output"] + correction["output"]
        if "discharge" in model:
            from .discharge_state import apply_discharge
            output_value = apply_discharge(model, output_value, outs[-1] if outs else output_value,
                                           values, previous, i == 0)
        outs.append(output_value)
        currents.append(state["current"] + correction["current"])
        previous = values
    axes[-1] = stop
    output = plan["roles"]["output"]
    return Waveform(tuple(axes), {output: "V", "VPROBE:p": "A"},
                    {output: tuple(outs), "VPROBE:p": tuple(currents)})


class SampledStatePlugin:
    __call__ = staticmethod(identify)

    @staticmethod
    def render_source(model):
        from .sampled_state_sv import render_model
        return render_model(model)

    @staticmethod
    def render_testbench(model, plan, case):
        from .sampled_state_sv import render_testbench
        return render_testbench(model, plan, case)
