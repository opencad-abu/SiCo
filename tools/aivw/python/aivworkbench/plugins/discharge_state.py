"""Bounded control-off output relaxation identified from public discharge.

The rate table is a function of output voltage, not time or case identity.
Only fixed supply/load and zero control are supported by this hypothesis.
"""

import math

from .sampled_state import interpolate


def fit_discharge(wave, *, output, start, stop, equilibrium, step, knots=129):
    if not 3 <= knots <= 257 or not start < stop or step <= 0:
        raise ValueError('invalid discharge identification bounds')
    top = wave.sample(output, start)
    if top <= equilibrium:
        raise ValueError('discharge excitation must exceed equilibrium')
    # Evaluate along the measured monotonic decay, excluding the near-equilibrium
    # numerical-noise region. A local centered difference estimates the ODE rate.
    rows = []
    t = start+step
    while t < stop-step:
        voltage = wave.sample(output, t)
        if voltage-equilibrium > .001:
            velocity = (wave.sample(output, t+step)-wave.sample(output, t-step))/(2*step)
            rate = -velocity/(voltage-equilibrium)
            if not math.isfinite(rate) or rate <= 0:
                raise ValueError('public discharge is not stable monotonic relaxation')
            rows.append([voltage, math.log(rate)])
        t += step
    rows.sort()
    if len(rows) < knots:
        raise ValueError('insufficient public discharge samples')
    # More voltage knots near the equilibrium preserve the rapidly changing
    # low-voltage relaxation without growing a waveform/time lookup table.
    levels = [equilibrium+(top-equilibrium)*(i/(knots-1))**2 for i in range(knots)]
    return {'basis': 'log_rate_by_output_voltage', 'equilibrium_v': equilibrium,
            'log_rate_knots': [[v, interpolate(rows, v)] for v in levels],
            'fit_scope': 'public_single_discharge_tail', 'qualification': 'NOT_ESTABLISHED'}


def advance_discharge(spec, previous, dt):
    if not math.isfinite(previous) or not math.isfinite(dt) or dt <= 0:
        raise ValueError('invalid discharge state or step')
    equilibrium = spec['equilibrium_v']
    rate = math.exp(interpolate(spec['log_rate_knots'], previous))
    # Exponential step preserves sign, boundedness and stable convergence even
    # when the sample duration is longer than the local time constant.
    return equilibrium+(previous-equilibrium)*math.exp(-dt*rate)


def apply_discharge(model, base, previous_output, values, previous_inputs, initialize):
    spec = model['discharge']
    index = spec['control_axis']
    if not initialize and values[index] == 0. and previous_inputs[index] == 0.:
        if (abs(values[0]-spec['supply_v']) > 1e-10 or
                abs(values[-1]-spec['load_conductance_s']) > 1e-12):
            raise ValueError('outside discharge supply/load domain')
        return advance_discharge(spec, previous_output, model['step_s'])
    return base


def render_discharge(model):
    spec = model['discharge']
    out = model['roles']['output']
    axis = spec['control_axis']
    knots = spec['log_rate_knots']
    lines = ['if(!initialize && x[%d]==0.0 && prev[%d]==0.0) begin' % (axis, axis),
             'if(x[0]<%.17g || x[0]>%.17g || load_conductance<%.17g || load_conductance>%.17g) $fatal(1,"DISCHARGE_DOMAIN");' %
             (spec['supply_v']-1e-10, spec['supply_v']+1e-10,
              spec['load_conductance_s']-1e-12, spec['load_conductance_s']+1e-12),
             'if(discharge_previous<=%.17g) discharge_lograte=%.17g;' % tuple(knots[0])]
    for a, b in zip(knots, knots[1:]):
        lines.append('else if(discharge_previous<=%.17g) discharge_lograte=%.17g+(discharge_previous-(%.17g))*%.17g;' %
                     (b[0], a[1], a[0], (b[1]-a[1])/(b[0]-a[0])))
    lines += ['else discharge_lograte=%.17g;' % knots[-1][1],
              '%s=%.17g+(discharge_previous-(%.17g))*$exp(-%.17g*$exp(discharge_lograte));' %
              (out, spec['equilibrium_v'], spec['equilibrium_v'], model['step_s']),
              'end', 'discharge_previous=%s;' % out]
    return lines
