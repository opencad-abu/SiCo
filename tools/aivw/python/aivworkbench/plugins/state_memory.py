"""Public-data residual memory bank with independent stable decay states.

States depend only on present/previous inputs and fixed sample duration. They
carry through sequential events and never use golden output as runtime state.
This hypothesis adds transient memory to an independently fitted static model.
"""

import math

from .sampled_state import _dot, _solve, features, inputs_at, simulate


def validate_constants(taus, dt, stop):
    if (not isinstance(taus, list) or not 2 <= len(taus) <= 5 or
            any(type(t) not in (int, float) or not math.isfinite(t) or
                not 0 <= t <= stop / 4 for t in taus) or
            taus != sorted(set(taus)) or taus[0] != 0 or
            any(0 < t < dt for t in taus)):
        raise ValueError("memory bank requires zero and distinct bounded stable constants")


def memory_features(model, values, previous):
    result = []
    for value, old, axis in zip(values, previous, model["axes"]):
        lo, hi = axis["knots"][0], axis["knots"][-1]
        coordinate = min(1., max(0., (value-lo)/(hi-lo)))
        slew = (value-old)/model["step_s"]/axis["slew_scale_per_s"]
        for direction in (1., -1.):
            for power in (.5, 1.):
                amplitude = max(0., direction*slew)**power
                result.extend(amplitude*coordinate**degree for degree in range(3))
    return result


def advance_memory(model, state, values, previous, *, initialize=False):
    if initialize:
        state[:] = [0.] * len(state)
        return
    phi = memory_features(model, values, previous)
    for block, tau in enumerate(model["memory"]["time_constants_s"]):
        a = math.exp(-model["step_s"]/tau) if tau else 0.
        for i, value in enumerate(phi):
            j = block*len(phi)+i
            state[j] = a*state[j]+(1-a)*value


def memory_values(model, state):
    return {channel: sum(x*c for x, c in zip(state, coeff))
            for channel, coeff in model["memory"]["coefficients"].items()}


def base_samples(model, plan, case, indices):
    """Exact base rollout samples; instantaneous channels need no intermediate steps."""
    dt = model['step_s']
    if any(channel['tau_s'] for channel in model['channels'].values()):
        wave = simulate(model, plan, case)
        names = {'output': plan['roles']['output'], 'current': 'VPROBE:p'}
        return {i: {k: wave.values[name][i] for k, name in names.items()} for i in indices}
    result = {}
    for i in indices:
        values = inputs_at(plan, case, i*dt)
        previous = inputs_at(plan, case, max(0, i-1)*dt)
        phi = features(model, values, previous, dt)
        result[i] = {k: _dot(phi, channel['coefficients']) for k, channel in model['channels'].items()}
    return result


def fit_memory(model, plan, waves):
    # Freeze the base before fitting corrections. No teacher forcing or hidden
    # cases; both fitting targets are residuals of a complete base rollout.
    base_model = dict(model)
    taus = model["identification_options"]["memory_time_constants_s"]
    n = len(model["axes"])*12*len(taus)
    model["memory"] = {"basis": "signed_slew_coordinate_polynomial_v1",
                       "time_constants_s": list(taus), "coefficients": {},
                       "fit": "public_rollout_residual", "relative_weight_floor_fraction": .01}
    matrices = {k: [[0.]*n for _ in range(n)] for k in ("output", "current")}
    rhs = {k: [0.]*n for k in matrices}
    dt, stop = model["step_s"], plan["analysis"]["stop_s"]
    names = {"output": plan["roles"]["output"], "current": "VPROBE:p"}
    row_count = 0
    for case in plan["cases"]:
        wave = waves[case["id"]]
        floors = {k: max(abs(v) for v in wave.values[name])*.01 for k, name in names.items()}
        selected = {round(i*stop/100/dt) for i in range(101)}
        for e in case["events"]:
            lo, hi = max(0, round(e["start_s"]/dt)-5), min(round(stop/dt), round((e["end_s"]+20e-6)/dt))
            selected.update(range(lo, hi+1, 5))
            for edge in (e["start_s"], e["end_s"]):
                selected.update(range(max(0, round(edge/dt)-5), min(round(stop/dt), round(edge/dt)+20)+1))
        base = base_samples(base_model, plan, case, selected)
        state = [0.]*n
        previous = inputs_at(plan, case, 0.)
        for i in range(round(stop/dt)+1):
            t = min(stop, i*dt)
            values = inputs_at(plan, case, t)
            advance_memory(model, state, values, previous, initialize=(i == 0))
            previous = values
            if i not in selected:
                continue
            # Tiny entries are omitted from fitting only, never runtime state.
            active = [(j, v) for j, v in enumerate(state) if abs(v) > 1e-14]
            if not active:
                continue
            row_count += 1
            for channel, name in names.items():
                golden = wave.sample(name, t)
                scale = max(abs(golden), floors[channel], 1e-15)
                target = (golden-base[i][channel])/scale
                weighted = [(j, v/scale) for j, v in active]
                matrix, b = matrices[channel], rhs[channel]
                for j, x in weighted:
                    b[j] += x*target
                    for k, y in weighted:
                        matrix[j][k] += x*y
    for channel, matrix in matrices.items():
        for j in range(n):
            matrix[j][j] += max(1e-8, matrix[j][j]*1e-7)
        # This compact bank is densely correlated. Diagonal scaling and pivoted
        # elimination avoid CG stagnation without relaxing the fit residual.
        scale = [math.sqrt(matrix[j][j]) for j in range(n)]
        normalized = [[x/scale[i]/scale[j] for j, x in enumerate(row)] for i, row in enumerate(matrix)]
        b = [x/s for x, s in zip(rhs[channel], scale)]
        answer = _solve(normalized, b)
        residual = max(abs(sum(x*y for x, y in zip(row, answer))-want) for row, want in zip(normalized, b))
        if residual > 1e-8*max(1., max(map(abs, b))):
            raise ValueError("memory fit normal-system residual exceeds numerical budget")
        model["memory"]["coefficients"][channel] = [x/s for x, s in zip(answer, scale)]
    model["memory"]["training_rows"] = row_count
    model["scope"] = "sampled_additive_base_with_independent_slew_memory_bank"


def render_memory(model):
    """SV statements called after base output update; same recurrence as Python."""
    lines, index = [], 0
    for i, axis in enumerate(model["axes"]):
        lo, hi = axis["knots"][0], axis["knots"][-1]
        coordinate = "((x[%d]-(%.17g))/%.17g)" % (i, lo, hi-lo)
        lines.append("slew=initialize ? 0.0 : (x[%d]-prev[%d])/%.17g;" % (i, i, model["step_s"]*axis["slew_scale_per_s"]))
        for direction in (1., -1.):
            for power in (.5, 1.):
                amplitude = "((%.1f*slew)>0.0 ? (%.1f*slew)**%.1f : 0.0)" % (direction, direction, power)
                for degree in range(3):
                    lines.append("memory_phi[%d]=%s*(%s**%d);" % (index, amplitude, coordinate, degree))
                    index += 1
    for block, tau in enumerate(model["memory"]["time_constants_s"]):
        a = math.exp(-model["step_s"]/tau) if tau else 0.
        for i in range(index):
            j = block*index+i
            lines.append("memory_state[%d]=initialize ? 0.0 : %.17g*memory_state[%d]+%.17g*memory_phi[%d];" % (j, a, j, 1-a, i))
    for channel, target in (("output", model["roles"]["output"]), ("current", "supply_current_a")):
        coeff = model["memory"]["coefficients"][channel]
        lines.append("%s=%s+%s;" % (target, target, "+".join("(%.17g)*memory_state[%d]" % (c, i) for i, c in enumerate(coeff))))
    return lines
